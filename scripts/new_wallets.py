"""Create two throwaway Sepolia wallets: a deployer and an issuer.

Writes .testnet_keys.json (owner-only, ignored by git) and prints the addresses only, never the keys.
Test networks only: these wallets must never hold real value.
"""
import json
import os
import sys
from pathlib import Path

from eth_account import Account

KEYS_PATH = Path(__file__).resolve().parent.parent / ".testnet_keys.json"


def main() -> None:
    if KEYS_PATH.exists():
        sys.exit(f"{KEYS_PATH.name} already exists; delete it first if you really want new wallets")
    deployer = Account.create()
    issuer = Account.create()
    payload = {
        "deployer_key": "0x" + bytes(deployer.key).hex(),
        "issuer_key": "0x" + bytes(issuer.key).hex(),
    }
    fd = os.open(KEYS_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"wrote {KEYS_PATH.name} (keys stay in that file)")
    print(f"deployer address (fund this with Sepolia ETH): {deployer.address}")
    print(f"issuer address (the deploy script tops this up):   {issuer.address}")


if __name__ == "__main__":
    main()
