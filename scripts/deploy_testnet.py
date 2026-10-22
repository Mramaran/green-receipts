"""Deploy the GreenReceipts contract to the Sepolia test network.

Needs, in the environment: GR_RPC_URL, a Sepolia JSON-RPC URL (it may contain a provider key).
Keys come from .testnet_keys.json (see scripts/new_wallets.py). The deployer wallet must hold
some Sepolia test ETH. Refuses to run on any network other than Sepolia.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

from app.chain import ARTIFACT_PATH, PACKAGED_ARTIFACT_PATH  # noqa: E402

SEPOLIA_CHAIN_ID = 11155111
KEYS_PATH = Path(__file__).resolve().parent.parent / ".testnet_keys.json"
ISSUER_MIN_BALANCE_WEI = Web3.to_wei(0.01, "ether")
ISSUER_TOP_UP_WEI = Web3.to_wei(0.02, "ether")


def send(w3: Web3, account, tx: dict) -> object:
    tx.setdefault("nonce", w3.eth.get_transaction_count(account.address, "pending"))
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)


def main() -> None:
    rpc = os.environ.get("GR_RPC_URL")
    if not rpc:
        sys.exit("set GR_RPC_URL to your Sepolia RPC URL first")
    if not KEYS_PATH.exists():
        sys.exit("run scripts/new_wallets.py first to create the deployer and issuer wallets")
    keys = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    deployer = Account.from_key(keys["deployer_key"])
    issuer = Account.from_key(keys["issuer_key"])

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        sys.exit("cannot reach the RPC URL in GR_RPC_URL")
    if w3.eth.chain_id != SEPOLIA_CHAIN_ID:
        sys.exit(f"chain id is {w3.eth.chain_id}, not Sepolia ({SEPOLIA_CHAIN_ID}); refusing to deploy")

    balance = w3.eth.get_balance(deployer.address)
    print(f"deployer {deployer.address} holds {Web3.from_wei(balance, 'ether')} Sepolia ETH")
    if balance < Web3.to_wei(0.05, "ether"):
        sys.exit("deployer needs at least 0.05 Sepolia ETH; get some from a faucet and run this again")

    artifact_path = ARTIFACT_PATH if ARTIFACT_PATH.exists() else PACKAGED_ARTIFACT_PATH
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    deploy_tx = contract.constructor(deployer.address, issuer.address).build_transaction(
        {"from": deployer.address}
    )
    mined = send(w3, deployer, deploy_tx)
    if mined.status != 1:
        sys.exit("deployment transaction failed")
    address = mined.contractAddress
    print(f"deployed GreenReceipts at {address}")

    if w3.eth.get_balance(issuer.address) < ISSUER_MIN_BALANCE_WEI:
        fee = w3.eth.gas_price * 2
        topup = {
            "from": deployer.address,
            "to": issuer.address,
            "value": ISSUER_TOP_UP_WEI,
            "chainId": SEPOLIA_CHAIN_ID,
            "gas": 21000,
            "maxFeePerGas": fee,
            "maxPriorityFeePerGas": Web3.to_wei(1, "gwei"),
        }
        send(w3, deployer, topup)
        print(f"topped up issuer {issuer.address} with {Web3.from_wei(ISSUER_TOP_UP_WEI, 'ether')} ETH for gas")

    print("\nSet these on Render (the issuer key is secret, keep it out of git and chat):")
    print(f"  GR_CONTRACT_ADDRESS = {address}")
    print("  GR_RPC_URL          = <your Sepolia RPC URL, secret>")
    print("  GR_PUBLIC_RPC_URL   = https://ethereum-sepolia-rpc.publicnode.com")
    print(f"  GR_ISSUER_KEY       = the issuer_key value inside {KEYS_PATH.name}")
    print(f"\nView it: https://sepolia.etherscan.io/address/{address}")


if __name__ == "__main__":
    main()
