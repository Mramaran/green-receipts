"""Deploy the GreenReceipts contract to a local Hardhat node and write deployment.json.

Run `npx hardhat node` inside chain/ first, build the contract with `npx hardhat build`,
then run this script. Test networks only: the node's accounts hold no real value.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

from app.chain import ARTIFACT_PATH, write_deployment  # noqa: E402
from app.keys import load_or_create_issuer_key  # noqa: E402

RPC_URL = "http://127.0.0.1:8545"


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        sys.exit(f"cannot reach a node at {RPC_URL}; run `npx hardhat node` in chain/ first")
    if not ARTIFACT_PATH.exists():
        sys.exit("contract not built; run `npx hardhat build` in chain/ first")

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    admin = w3.eth.accounts[0]
    issuer = Account.from_key(load_or_create_issuer_key()).address

    funding = w3.eth.send_transaction({"from": admin, "to": issuer, "value": w3.to_wei(10, "ether")})
    w3.eth.wait_for_transaction_receipt(funding)

    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    deploy_tx = contract.constructor(admin, issuer).transact({"from": admin})
    mined = w3.eth.wait_for_transaction_receipt(deploy_tx)

    write_deployment(mined.contractAddress, RPC_URL, w3.eth.chain_id, issuer)
    print(f"deployed GreenReceipts at {mined.contractAddress}")
    print(f"issuer {issuer} (funded with 10 test ETH)")


if __name__ == "__main__":
    main()
