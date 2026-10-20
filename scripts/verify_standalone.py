#!/usr/bin/env python
"""Check a Green Receipts receipt without our server.

This script does not import or call any of the service's code. It needs only:
  * the receipt and its signature (the file the dashboard downloads, or the /v1/chat response),
  * a node's JSON-RPC address, and
  * the contract address.
It re-derives everything itself, so it cannot share a bug with the service.

Checks, in order:
  1. the receipt can be rendered as canonical JSON and hashed (keccak256)
  2. the signature recovers a signer (Ethereum signed-message format)
  3. the contract holds a record for that hash, and its recorded issuer is that signer
  4. the recorded organisation and emissions range match what the receipt states

Usage:
  python scripts/verify_standalone.py receipt.json
  python scripts/verify_standalone.py receipt.json --rpc http://127.0.0.1:8545 --contract 0x...
  python scripts/verify_standalone.py bare_receipt.json --signature 0x...

Exit code: 0 all checks pass, 1 a check failed, 2 usage or connection problem.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from decimal import Decimal

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address
from web3 import Web3

NG_PER_G = 1_000_000_000

GET_RECORD_ABI = [
    {
        "type": "function",
        "name": "getRecord",
        "stateMutability": "view",
        "inputs": [{"name": "receiptHash", "type": "bytes32"}],
        "outputs": [
            {
                "type": "tuple",
                "components": [
                    {"name": "orgId", "type": "bytes32"},
                    {"name": "recordedAt", "type": "uint64"},
                    {"name": "issuer", "type": "address"},
                    {"name": "gco2eMinNg", "type": "uint128"},
                    {"name": "gco2eMaxNg", "type": "uint128"},
                ],
            }
        ],
    }
]


# Canonical form: keys sorted, no whitespace, ASCII only, numbers as plain decimals
# (no exponent, no trailing zeros), so the hash does not depend on a language's float printing.
def _number(value) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def canonical_json(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("keys must be strings")
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=True)}:{canonical_json(value[k])}" for k in sorted(value)) + "}"
    raise ValueError(f"unsupported type {type(value).__name__}")


def receipt_hash(receipt: dict) -> bytes:
    return keccak(canonical_json(receipt).encode("ascii"))


def org_id(org: str) -> bytes:
    return keccak(org.encode("utf-8"))


def to_ng(grams: float) -> int:
    return round(grams * NG_PER_G)


def load_input(path: str, rpc: str | None, contract: str | None, signature: str | None) -> dict:
    """Accepts the dashboard download, a full /v1/chat response, or a bare receipt."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    proof = data.get("proof", {}) if isinstance(data, dict) else {}
    receipt = data.get("receipt", data) if isinstance(data, dict) else data
    found = {
        "receipt": receipt,
        "signature": signature or data.get("signature") or proof.get("signature"),
        "rpc": rpc or data.get("rpc_url") or proof.get("rpc_url"),
        "contract": contract or data.get("contract") or proof.get("contract"),
    }
    missing = [name for name, value in found.items() if not value]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}: pass it as an option or use the file the dashboard downloads")
    return found


def fetch_record(rpc_url: str, contract_address: str, digest: bytes) -> dict | None:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    if not w3.is_connected():
        raise ConnectionError(f"cannot reach a node at {rpc_url}")
    address = to_checksum_address(contract_address)
    if len(w3.eth.get_code(address)) == 0:
        raise ConnectionError(f"no contract is deployed at {address} on that node")
    contract = w3.eth.contract(address=address, abi=GET_RECORD_ABI)
    org, recorded_at, issuer, low, high = contract.functions.getRecord(digest).call()
    if int(recorded_at) == 0:
        return None
    return {"org_id": bytes(org), "recorded_at": int(recorded_at), "issuer": to_checksum_address(issuer),
            "min_ng": int(low), "max_ng": int(high)}


def run_checks(receipt: dict, signature: str, fetch) -> list[tuple[str, bool, str]]:
    """`fetch(digest)` returns the on-chain record dict, or None when nothing is recorded."""
    try:
        digest = receipt_hash(receipt)
    except (ValueError, TypeError, RecursionError) as exc:
        return [("receipt is well formed", False, str(exc) or "could not be hashed")]
    results = [("receipt is well formed", True, "0x" + digest.hex())]

    try:
        signer = Account.recover_message(encode_defunct(primitive=digest), signature=signature)
        results.append(("signature recovers a signer", True, signer))
    except Exception:
        signer = None
        results.append(("signature recovers a signer", False, "signature is malformed"))

    record = fetch(digest)
    if record is None:
        results.append(("signer matches the recorded issuer", False, "no on-chain record for this hash"))
        results.append(("hash is recorded on-chain", False, "the contract has nothing for this exact receipt"))
        return results

    if signer is None:
        results.append(("signer matches the recorded issuer", False, "no signer to compare"))
    elif signer == record["issuer"]:
        results.append(("signer matches the recorded issuer", True, f"{signer} recorded it"))
    else:
        results.append(("signer matches the recorded issuer", False, f"signed by {signer}, recorded by {record['issuer']}"))
    results.append(("hash is recorded on-chain", True, f"recorded at unix time {record['recorded_at']}"))

    try:
        same_org = record["org_id"] == org_id(receipt["org"])
        low, high = to_ng(receipt["emissions_gco2e"]["min"]), to_ng(receipt["emissions_gco2e"]["max"])
    except (KeyError, TypeError, ValueError, AttributeError):
        results.append(("recorded values match the receipt", False, "receipt has no organisation or range"))
        return results
    if not same_org:
        results.append(("recorded values match the receipt", False, "the chain records a different organisation"))
    elif (record["min_ng"], record["max_ng"]) != (low, high):
        results.append(("recorded values match the receipt", False,
                        f"chain {record['min_ng']}-{record['max_ng']} ng, receipt {low}-{high} ng"))
    else:
        results.append(("recorded values match the receipt", True, "organisation and range agree"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Green Receipts receipt against a chain, without the service.")
    parser.add_argument("file", help="receipt JSON (dashboard download, /v1/chat response, or a bare receipt)")
    parser.add_argument("--rpc", help="node JSON-RPC URL (default: from the file)")
    parser.add_argument("--contract", help="contract address (default: from the file)")
    parser.add_argument("--signature", help="receipt signature (default: from the file)")
    args = parser.parse_args(argv)

    try:
        given = load_input(args.file, args.rpc, args.contract, args.signature)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    try:
        results = run_checks(given["receipt"], given["signature"],
                             lambda digest: fetch_record(given["rpc"], given["contract"], digest))
    except ConnectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    verified = all(ok for _, ok, _ in results)
    print("VERIFIED" if verified else "REJECTED")
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
