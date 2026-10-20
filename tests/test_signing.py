import pytest
from eth_account import Account

from app.signing import canonical_json, receipt_hash, recover_signer, sign_hash


def test_canonical_json_sorts_keys_and_trims_numbers():
    assert canonical_json({"b": 1, "a": [2.5, 3.0, "x"]}) == '{"a":[2.5,3,"x"],"b":1}'


def test_small_floats_never_use_exponent_notation():
    assert canonical_json(2.98151e-06) == "0.00000298151"


def test_key_order_does_not_change_the_hash():
    assert receipt_hash({"a": 1, "b": 2}) == receipt_hash({"b": 2, "a": 1})


def test_booleans_and_null_are_rendered_as_json():
    assert canonical_json({"ok": True, "no": False, "none": None}) == '{"no":false,"none":null,"ok":true}'


def test_non_finite_numbers_are_rejected():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_hash_is_32_bytes_and_changes_when_a_field_changes():
    original = {"emissions": {"min": 0.00024, "max": 0.00029}}
    edited = {"emissions": {"min": 0.00024, "max": 0.00028}}
    assert len(receipt_hash(original)) == 32
    assert receipt_hash(original) != receipt_hash(edited)


def test_signature_recovers_the_signing_address():
    key = "0x" + bytes(Account.create().key).hex()
    digest = receipt_hash({"a": 1})
    signature = sign_hash(digest, key)
    assert recover_signer(digest, signature) == Account.from_key(key).address


def test_signature_from_another_key_recovers_a_different_address():
    key = "0x" + bytes(Account.create().key).hex()
    other = "0x" + bytes(Account.create().key).hex()
    digest = receipt_hash({"a": 1})
    assert recover_signer(digest, sign_hash(digest, other)) != Account.from_key(key).address
