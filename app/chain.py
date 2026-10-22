import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from eth_account import Account
from eth_utils import keccak, to_checksum_address
from web3 import Web3
from web3.exceptions import TimeExhausted

log = logging.getLogger("green_receipts.chain")

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_PATH = ROOT / "deployment.json"
ARTIFACT_PATH = ROOT / "chain" / "artifacts" / "contracts" / "GreenReceipts.sol" / "GreenReceipts.json"
# Compiled contract committed to git so a host without Node can still load the ABI.
PACKAGED_ARTIFACT_PATH = ROOT / "chain" / "GreenReceipts.artifact.json"
NG_PER_G = 1_000_000_000
# Nodes report custom errors as a 4-byte selector, not by name.
ALREADY_RECORDED_SELECTOR = "0x" + keccak(text="AlreadyRecorded(bytes32)")[:4].hex()


class ChainError(RuntimeError):
    """A chain problem. Messages are safe to show to callers; details go to the log."""


class AlreadyRecorded(ChainError):
    pass


class ChainTimeout(ChainError):
    """The transaction was sent but not confirmed in time. It may still be recorded."""


def org_id(org: str) -> bytes:
    return keccak(org.encode("utf-8"))


def grams_to_ng(grams: float) -> int:
    """Whole nanograms. A value below 0.5 ng records as 0."""
    return round(grams * NG_PER_G)


@dataclass(frozen=True)
class OrgTotals:
    receipts: int
    gco2e_min: float
    gco2e_max: float


@dataclass(frozen=True)
class RecordInfo:
    """What the chain holds for one receipt hash."""

    org_id: bytes
    recorded_at: int
    issuer: str
    gco2e_min_ng: int
    gco2e_max_ng: int


class ChainClient(Protocol):
    description: str

    def record(self, receipt_hash: bytes, org: str, gco2e_min: float, gco2e_max: float) -> str: ...

    def get_record(self, receipt_hash: bytes) -> RecordInfo | None: ...

    def is_recorded(self, receipt_hash: bytes) -> bool: ...

    def has_issuer_role(self, address: str) -> bool: ...

    def org_totals(self, org: str) -> OrgTotals: ...


class InMemoryChain:
    """Test double for recording, lookup and totals only.

    It does not model the contract's role check on writes or integer bounds. Those are
    covered by the Hardhat tests and by tests/test_web3_chain.py against a real node.
    Records are attributed to the first issuer, like a transaction sent from that account.
    """

    description = "in-memory (tests only)"

    def __init__(self, issuers: set[str]):
        self._issuers = {to_checksum_address(a) for a in issuers}
        self._sender = sorted(self._issuers)[0] if self._issuers else None
        self._records: dict[bytes, RecordInfo] = {}
        self._lock = threading.Lock()

    def revoke(self, address: str) -> None:
        """Remove the issuer role. Existing records keep their recorded issuer."""
        self._issuers.discard(to_checksum_address(address))

    def record(self, receipt_hash: bytes, org: str, gco2e_min: float, gco2e_max: float) -> str:
        low, high = grams_to_ng(gco2e_min), grams_to_ng(gco2e_max)
        if low > high:
            raise ChainError("invalid range")
        with self._lock:
            if receipt_hash in self._records:
                raise AlreadyRecorded(receipt_hash.hex())
            self._records[receipt_hash] = RecordInfo(
                org_id=org_id(org),
                recorded_at=int(time.time()),
                issuer=self._sender or "",
                gco2e_min_ng=low,
                gco2e_max_ng=high,
            )
            return f"memory-tx-{len(self._records)}"

    def get_record(self, receipt_hash: bytes) -> RecordInfo | None:
        return self._records.get(receipt_hash)

    def is_recorded(self, receipt_hash: bytes) -> bool:
        return receipt_hash in self._records

    def has_issuer_role(self, address: str) -> bool:
        return to_checksum_address(address) in self._issuers

    def org_totals(self, org: str) -> OrgTotals:
        wanted = org_id(org)
        rows = [r for r in self._records.values() if r.org_id == wanted]
        return OrgTotals(
            receipts=len(rows),
            gco2e_min=sum(r.gco2e_min_ng for r in rows) / NG_PER_G,
            gco2e_max=sum(r.gco2e_max_ng for r in rows) / NG_PER_G,
        )


class Web3Chain:
    """The deployed GreenReceipts contract, reached over JSON-RPC (local node)."""

    def __init__(
        self,
        rpc_url: str,
        contract_address: str,
        abi: list,
        issuer_key: str,
        public_rpc_url: str | None = None,
    ):
        # rpc_url may carry a provider API key, so only public_rpc_url is ever shown to callers.
        self.rpc_url = public_rpc_url or rpc_url
        self.address = to_checksum_address(contract_address)
        self.description = f"contract {self.address} via {self.rpc_url}"
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        self._account = Account.from_key(issuer_key)
        self._contract = self._w3.eth.contract(address=self.address, abi=abi)
        self._lock = threading.Lock()

    @classmethod
    def from_deployment(cls, issuer_key: str) -> "Web3Chain":
        """Settings come from GR_RPC_URL and GR_CONTRACT_ADDRESS when set (hosted), else deployment.json."""
        abi_path = ARTIFACT_PATH if ARTIFACT_PATH.exists() else PACKAGED_ARTIFACT_PATH
        abi = json.loads(abi_path.read_text(encoding="utf-8"))["abi"]
        env_rpc = os.environ.get("GR_RPC_URL")
        env_address = os.environ.get("GR_CONTRACT_ADDRESS")
        if env_rpc and env_address:
            return cls(env_rpc, env_address, abi, issuer_key, os.environ.get("GR_PUBLIC_RPC_URL"))
        deployment = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
        return cls(deployment["rpc_url"], deployment["address"], abi, issuer_key)

    def is_connected(self) -> bool:
        try:
            return bool(self._w3.is_connected())
        except Exception:
            return False

    def healthy(self) -> bool:
        """Node reachable and the contract still deployed at this address.

        A restarted local node loses its state, so the address can stop holding code.
        """
        try:
            return bool(self._w3.is_connected()) and len(self._w3.eth.get_code(self.address)) > 0
        except Exception:
            return False

    def _read(self, call):
        try:
            return call.call()
        except Exception as exc:
            log.warning("chain read failed: %s", exc)
            raise ChainError("could not read from the chain") from exc

    def record(self, receipt_hash: bytes, org: str, gco2e_min: float, gco2e_max: float) -> str:
        call = self._contract.functions.recordReceipt(
            receipt_hash, org_id(org), grams_to_ng(gco2e_min), grams_to_ng(gco2e_max)
        )
        with self._lock:
            try:
                tx = call.build_transaction(
                    {
                        "from": self._account.address,
                        "nonce": self._w3.eth.get_transaction_count(self._account.address, "pending"),
                    }
                )
                signed = self._account.sign_transaction(tx)
                tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            except Exception as exc:
                if ALREADY_RECORDED_SELECTOR in str(exc):
                    raise AlreadyRecorded(receipt_hash.hex()) from exc
                log.warning("chain write failed: %s", exc)
                raise ChainError("could not write to the chain") from exc
            try:
                mined = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            except TimeExhausted as exc:
                log.warning("transaction %s not confirmed in time", bytes(tx_hash).hex())
                raise ChainTimeout("the chain did not confirm in time") from exc
            except Exception as exc:
                log.warning("waiting for confirmation failed: %s", exc)
                raise ChainError("could not confirm the transaction") from exc
        if mined.status != 1:
            raise ChainError("the transaction was reverted")
        return "0x" + bytes(tx_hash).hex()

    def get_record(self, receipt_hash: bytes) -> RecordInfo | None:
        org, recorded_at, issuer, low, high = self._read(self._contract.functions.getRecord(receipt_hash))
        if int(recorded_at) == 0:
            return None
        return RecordInfo(
            org_id=bytes(org),
            recorded_at=int(recorded_at),
            issuer=to_checksum_address(issuer),
            gco2e_min_ng=int(low),
            gco2e_max_ng=int(high),
        )

    def is_recorded(self, receipt_hash: bytes) -> bool:
        return bool(self._read(self._contract.functions.isRecorded(receipt_hash)))

    def has_issuer_role(self, address: str) -> bool:
        role = self._read(self._contract.functions.ISSUER_ROLE())
        return bool(self._read(self._contract.functions.hasRole(role, to_checksum_address(address))))

    def org_totals(self, org: str) -> OrgTotals:
        key = org_id(org)
        return OrgTotals(
            receipts=int(self._read(self._contract.functions.orgReceiptCount(key))),
            gco2e_min=self._read(self._contract.functions.orgTotalMinNg(key)) / NG_PER_G,
            gco2e_max=self._read(self._contract.functions.orgTotalMaxNg(key)) / NG_PER_G,
        )


def write_deployment(address: str, rpc_url: str, chain_id: int, issuer: str) -> None:
    DEPLOYMENT_PATH.write_text(
        json.dumps(
            {
                "address": address,
                "rpc_url": rpc_url,
                "chain_id": chain_id,
                "issuer": issuer,
                "deployed_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
