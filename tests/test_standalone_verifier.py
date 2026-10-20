import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from app import signing
from app.chain import DEPLOYMENT_PATH, Web3Chain, grams_to_ng
from app.keys import load_or_create_issuer_key
from app.main import app

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_standalone.py"
_spec = importlib.util.spec_from_file_location("verify_standalone", SCRIPT)
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)

client = TestClient(app)


def _issue() -> dict:
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "hello", "region": "uk-south"})
    assert response.status_code == 200
    return response.json()


def _record_for(chain, receipt: dict):
    """The on-chain record for this receipt, in the shape the standalone script reads."""
    record = chain.get_record(signing.receipt_hash(receipt))
    if record is None:
        return None
    return {"org_id": record.org_id, "recorded_at": record.recorded_at, "issuer": record.issuer,
            "min_ng": record.gco2e_min_ng, "max_ng": record.gco2e_max_ng}


def _names(results, passed: bool) -> set[str]:
    return {name for name, ok, _ in results if ok is passed}


def test_the_verifier_does_not_import_the_service():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source


@pytest.mark.parametrize(
    "value",
    [
        {"b": 1, "a": [2.5, 3.0, "x"]},
        2.98151e-06,
        1e21,
        -0.0,
        123456789.123456789,
        {"nested": {"k": None, "t": True, "f": False}},
        "unicode é ☃ and \"quotes\"",
        [],
        {},
        0,
    ],
)
def test_canonical_json_matches_the_service(value):
    assert sv.canonical_json(value) == signing.canonical_json(value)


def test_receipt_hash_matches_the_service_for_a_real_receipt():
    receipt = _issue()["receipt"]
    assert sv.receipt_hash(receipt) == signing.receipt_hash(receipt)


def test_a_genuine_receipt_passes_every_check(chain):
    body = _issue()
    results = sv.run_checks(body["receipt"], body["proof"]["signature"], lambda digest: _record_for(chain, body["receipt"]))
    assert _names(results, False) == set()
    assert len(results) == 5


def test_a_tampered_receipt_is_rejected(chain):
    body = _issue()
    tampered = json.loads(json.dumps(body["receipt"]))
    tampered["emissions_gco2e"]["max"] = tampered["emissions_gco2e"]["max"] / 10
    # The edited receipt hashes to something the chain has never seen.
    results = sv.run_checks(tampered, body["proof"]["signature"], lambda digest: chain.get_record(digest))
    assert _names(results, False) == {"signer matches the recorded issuer", "hash is recorded on-chain"}


def test_a_different_signer_is_rejected(chain):
    body = _issue()
    other = "0x" + bytes(Account.create().key).hex()
    forged = signing.sign_hash(signing.receipt_hash(body["receipt"]), other)
    results = sv.run_checks(body["receipt"], forged, lambda digest: _record_for(chain, body["receipt"]))
    assert _names(results, False) == {"signer matches the recorded issuer"}


def test_a_record_with_a_different_range_is_rejected(chain):
    body = _issue()
    record = _record_for(chain, body["receipt"])
    record["max_ng"] -= 1
    results = sv.run_checks(body["receipt"], body["proof"]["signature"], lambda digest: record)
    assert _names(results, False) == {"recorded values match the receipt"}


def test_a_record_for_another_organisation_is_rejected(chain):
    body = _issue()
    record = _record_for(chain, body["receipt"])
    record["org_id"] = sv.org_id("rival")
    results = sv.run_checks(body["receipt"], body["proof"]["signature"], lambda digest: record)
    assert _names(results, False) == {"recorded values match the receipt"}


def test_a_malformed_signature_is_rejected(chain):
    body = _issue()
    results = sv.run_checks(body["receipt"], "0x1234", lambda digest: _record_for(chain, body["receipt"]))
    assert "signature recovers a signer" in _names(results, False)


def test_the_range_is_compared_in_the_same_units_the_service_records(chain):
    body = _issue()
    emissions = body["receipt"]["emissions_gco2e"]
    record = _record_for(chain, body["receipt"])
    assert (record["min_ng"], record["max_ng"]) == (sv.to_ng(emissions["min"]), sv.to_ng(emissions["max"]))
    assert sv.to_ng(emissions["min"]) == grams_to_ng(emissions["min"])


def test_input_shapes_are_all_understood(tmp_path):
    receipt = {"receipt_id": "r", "org": "acme"}
    shapes = {
        "download": {"receipt": receipt, "signature": "0xsig", "contract": "0xc", "rpc_url": "http://rpc"},
        "response": {"answer": "x", "receipt": receipt, "proof": {"signature": "0xsig", "contract": "0xc", "rpc_url": "http://rpc"}},
    }
    for name, data in shapes.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = sv.load_input(str(path), None, None, None)
        assert loaded == {"receipt": receipt, "signature": "0xsig", "rpc": "http://rpc", "contract": "0xc"}

    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(receipt), encoding="utf-8")
    loaded = sv.load_input(str(bare), "http://other", "0xother", "0xmine")
    assert loaded == {"receipt": receipt, "signature": "0xmine", "rpc": "http://other", "contract": "0xother"}
    with pytest.raises(SystemExit):
        sv.load_input(str(bare), None, None, None)


def test_exit_codes(chain, tmp_path, monkeypatch, capsys):
    body = _issue()
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"receipt": body["receipt"], "signature": body["proof"]["signature"],
                                "contract": "0xc", "rpc_url": "http://rpc"}), encoding="utf-8")

    monkeypatch.setattr(sv, "fetch_record", lambda rpc, contract, digest: _record_for(chain, body["receipt"]))
    assert sv.main([str(path)]) == 0
    assert "VERIFIED" in capsys.readouterr().out

    monkeypatch.setattr(sv, "fetch_record", lambda rpc, contract, digest: None)
    assert sv.main([str(path)]) == 1
    assert "REJECTED" in capsys.readouterr().out

    def unreachable(rpc, contract, digest):
        raise ConnectionError("cannot reach a node")

    monkeypatch.setattr(sv, "fetch_record", unreachable)
    assert sv.main([str(path)]) == 2


def test_against_a_real_node(tmp_path):
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json; start a local node and run scripts/deploy_local.py")
    key = load_or_create_issuer_key()
    chain = Web3Chain.from_deployment(key)
    if not chain.healthy():
        pytest.skip("local chain node is not reachable")

    org = f"sv-{uuid.uuid4().hex[:8]}"
    receipt = {"receipt_id": str(uuid.uuid4()), "org": org, "emissions_gco2e": {"min": 0.000244, "max": 0.000288}}
    digest = signing.receipt_hash(receipt)
    chain.record(digest, org, 0.000244, 0.000288)
    signature = signing.sign_hash(digest, key)

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"receipt": receipt, "signature": signature, "contract": chain.address, "rpc_url": chain.rpc_url}), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), str(good)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "VERIFIED" in run.stdout

    edited = json.loads(good.read_text(encoding="utf-8"))
    edited["receipt"]["emissions_gco2e"]["max"] = 0.0000288
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(edited), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "REJECTED" in run.stdout
