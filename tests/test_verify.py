import copy

import pytest
from eth_account import Account

from app.chain import AlreadyRecorded
from app.signing import receipt_hash, sign_hash
from app.verify import verify_receipt

CLAIM_MIN, CLAIM_MAX = 0.00024, 0.00029


def _issue(chain, key, org="acme", receipt_id="r1", record_org=None, record_range=(CLAIM_MIN, CLAIM_MAX)):
    receipt = {
        "receipt_id": receipt_id,
        "org": org,
        "energy_kwh": {"min": 3e-06, "max": 3.5e-06},
        "emissions_gco2e": {"min": CLAIM_MIN, "max": CLAIM_MAX},
    }
    digest = receipt_hash(receipt)
    signature = sign_hash(digest, key)
    chain.record(digest, record_org or org, *record_range)
    return receipt, signature


def _failed(result):
    return {c["name"] for c in result["checks"] if not c["passed"]}


def test_genuine_receipt_passes_all_checks(chain, issuer_key):
    receipt, signature = _issue(chain, issuer_key)
    result = verify_receipt(receipt, signature, chain)
    assert result["valid"] is True
    assert _failed(result) == set()
    assert [c["name"] for c in result["checks"]] == [
        "receipt_well_formed",
        "signature_recovers_a_signer",
        "signer_matches_recorded_issuer",
        "hash_recorded_on_chain",
        "recorded_values_match_receipt",
    ]


def test_tampered_receipt_is_rejected(chain, issuer_key):
    receipt, signature = _issue(chain, issuer_key)
    tampered = copy.deepcopy(receipt)
    tampered["emissions_gco2e"]["max"] = 0.00001
    result = verify_receipt(tampered, signature, chain)
    assert result["valid"] is False
    assert _failed(result) == {"signer_matches_recorded_issuer", "hash_recorded_on_chain"}


def test_changing_the_org_is_rejected(chain, issuer_key):
    receipt, signature = _issue(chain, issuer_key)
    tampered = {**receipt, "org": "rival"}
    assert verify_receipt(tampered, signature, chain)["valid"] is False


def test_signature_from_a_different_key_is_rejected(chain, issuer_key):
    receipt, _ = _issue(chain, issuer_key)
    stranger = "0x" + bytes(Account.create().key).hex()
    result = verify_receipt(receipt, sign_hash(receipt_hash(receipt), stranger), chain)
    assert result["valid"] is False
    assert _failed(result) == {"signer_matches_recorded_issuer"}


def test_signed_but_never_recorded_receipt_is_rejected(chain, issuer_key):
    receipt = {"receipt_id": "ghost", "org": "acme"}
    signature = sign_hash(receipt_hash(receipt), issuer_key)
    result = verify_receipt(receipt, signature, chain)
    assert result["valid"] is False
    assert _failed(result) == {"signer_matches_recorded_issuer", "hash_recorded_on_chain"}


def test_malformed_signature_is_rejected(chain, issuer_key):
    receipt, _ = _issue(chain, issuer_key)
    result = verify_receipt(receipt, "0x1234", chain)
    assert result["valid"] is False
    assert "signature_recovers_a_signer" in _failed(result)
    assert "signer_matches_recorded_issuer" in _failed(result)


def test_unhashable_receipt_is_rejected(chain):
    result = verify_receipt({"x": float("inf")}, "0x00", chain)
    assert result["valid"] is False
    assert result["receipt_hash"] is None


def test_receipt_stays_valid_after_the_issuer_role_is_revoked(chain, issuer_key):
    receipt, signature = _issue(chain, issuer_key)
    chain.revoke(Account.from_key(issuer_key).address)
    assert chain.has_issuer_role(Account.from_key(issuer_key).address) is False
    result = verify_receipt(receipt, signature, chain)
    assert result["valid"] is True, result


def test_a_chain_record_that_states_a_different_range_is_caught(chain, issuer_key):
    # The issuer signs one range but records a smaller one on-chain.
    receipt, signature = _issue(chain, issuer_key, record_range=(0.0001, 0.0002))
    result = verify_receipt(receipt, signature, chain)
    assert result["valid"] is False
    assert _failed(result) == {"recorded_values_match_receipt"}


def test_a_chain_record_filed_under_another_organisation_is_caught(chain, issuer_key):
    receipt, signature = _issue(chain, issuer_key, record_org="rival")
    result = verify_receipt(receipt, signature, chain)
    assert result["valid"] is False
    assert _failed(result) == {"recorded_values_match_receipt"}


def test_a_receipt_without_a_range_cannot_pass_the_value_check(chain, issuer_key):
    receipt = {"receipt_id": "no-range", "org": "acme"}
    digest = receipt_hash(receipt)
    chain.record(digest, "acme", 0.0001, 0.0002)
    result = verify_receipt(receipt, sign_hash(digest, issuer_key), chain)
    assert result["valid"] is False
    assert _failed(result) == {"recorded_values_match_receipt"}


def test_org_totals_add_up(chain, issuer_key):
    _issue(chain, issuer_key, receipt_id="a")
    _issue(chain, issuer_key, receipt_id="b")
    totals = chain.org_totals("acme")
    assert totals.receipts == 2
    assert totals.gco2e_min == pytest.approx(0.00048)
    assert totals.gco2e_max == pytest.approx(0.00058)


def test_recording_the_same_receipt_twice_fails(chain, issuer_key):
    receipt, _ = _issue(chain, issuer_key)
    with pytest.raises(AlreadyRecorded):
        chain.record(receipt_hash(receipt), "acme", CLAIM_MIN, CLAIM_MAX)
