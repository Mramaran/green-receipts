import copy

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from app.chain import grams_to_ng
from app.main import app
from app.signing import receipt_hash

client = TestClient(app)


def _chat(region: str, prompt: str = "Explain carbon markets in two sentences.") -> dict:
    response = client.post("/v1/chat", json={"org": "acme", "prompt": prompt, "region": region})
    assert response.status_code == 200, response.text
    return response.json()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_regions_listed():
    regions = client.get("/regions").json()["regions"]
    assert "eu-north" in regions and "ap-south" in regions


def test_chat_returns_answer_and_complete_receipt():
    body = _chat("eu-north")
    receipt = body["receipt"]
    assert body["answer"]
    for key in ("schema", "receipt_id", "org", "issued_at", "request", "region", "grid",
                "energy_kwh", "emissions_gco2e", "method"):
        assert key in receipt, key
    assert receipt["org"] == "acme"
    assert receipt["request"]["is_mock"] is True
    assert receipt["grid"]["source"] == "bundled-sample-v1"


def test_receipt_states_range_method_and_declared_region():
    receipt = _chat("uk-south")["receipt"]
    emissions = receipt["emissions_gco2e"]
    assert 0 < emissions["min"] <= emissions["max"]
    assert receipt["method"]["accuracy_statement"] == "Estimate with a stated range. Not audit-grade."
    assert receipt["method"]["intensity_uncertainty_pct"] == 15
    assert "Declared by the caller" in receipt["region"]["note"]


def test_cleaner_region_gives_lower_emissions_for_same_prompt():
    clean = _chat("eu-north")["receipt"]["emissions_gco2e"]
    dirty = _chat("ap-south")["receipt"]["emissions_gco2e"]
    assert clean["max"] < dirty["min"]


def test_receipt_ids_are_unique():
    first = _chat("eu-west")["receipt"]["receipt_id"]
    second = _chat("eu-west")["receipt"]["receipt_id"]
    assert first != second


def test_unknown_region_is_rejected():
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "hi", "region": "mars-1"})
    assert response.status_code == 422
    assert "valid regions" in response.json()["detail"]


def test_empty_prompt_is_rejected():
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "", "region": "eu-north"})
    assert response.status_code == 422


def _verify(receipt: dict, signature: str) -> dict:
    response = client.post("/v1/verify", json={"receipt": receipt, "signature": signature})
    assert response.status_code == 200, response.text
    return response.json()


def test_chat_returns_a_proof_that_matches_the_receipt(issuer_key):
    body = _chat("eu-west")
    proof = body["proof"]
    assert proof["receipt_hash"] == "0x" + receipt_hash(body["receipt"]).hex()
    assert proof["issuer"] == Account.from_key(issuer_key).address
    assert proof["signature"].startswith("0x") and proof["tx_hash"]


def test_a_genuine_receipt_verifies():
    body = _chat("eu-west")
    result = _verify(body["receipt"], body["proof"]["signature"])
    assert result["valid"] is True
    assert result["receipt_hash"] == body["proof"]["receipt_hash"]


def test_an_edited_receipt_is_rejected_by_the_verify_endpoint():
    body = _chat("ap-south")
    edited = copy.deepcopy(body["receipt"])
    edited["emissions_gco2e"]["max"] = edited["emissions_gco2e"]["max"] / 10
    result = _verify(edited, body["proof"]["signature"])
    assert result["valid"] is False
    assert any(c["name"] == "hash_recorded_on_chain" and not c["passed"] for c in result["checks"])


def test_org_totals_follow_the_recorded_receipts():
    first = _chat("eu-north")["receipt"]["emissions_gco2e"]
    second = _chat("eu-north")["receipt"]["emissions_gco2e"]
    totals = client.get("/v1/orgs/acme/totals").json()
    assert totals["receipts"] == 2
    # The chain stores whole nanograms, so the expected total is the sum of the rounded values.
    assert totals["gco2e_min"] == pytest.approx((grams_to_ng(first["min"]) + grams_to_ng(second["min"])) / 1e9, abs=1e-12)
    assert totals["gco2e_max"] == pytest.approx((grams_to_ng(first["max"]) + grams_to_ng(second["max"])) / 1e9, abs=1e-12)
