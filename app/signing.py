import json
import math
from decimal import Decimal

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak


def _render_number(value: int | float) -> str:
    """Plain decimal, no exponent, no trailing zeros, so the hash does not depend on
    how a language formats floats."""
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("receipt contains a non-finite number")
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def canonical_json(value) -> str:
    """Deterministic JSON: keys sorted, no whitespace, ASCII only, plain-decimal numbers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _render_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("receipt keys must be strings")
        items = (f"{json.dumps(key, ensure_ascii=True)}:{canonical_json(value[key])}" for key in sorted(value))
        return "{" + ",".join(items) + "}"
    raise ValueError(f"unsupported value of type {type(value).__name__}")


def receipt_hash(receipt: dict) -> bytes:
    return keccak(canonical_json(receipt).encode("ascii"))


def sign_hash(digest: bytes, private_key: str) -> str:
    signed = Account.sign_message(encode_defunct(primitive=digest), private_key)
    return "0x" + bytes(signed.signature).hex()


def recover_signer(digest: bytes, signature: str) -> str:
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)
