import json
from pathlib import Path
from urllib.parse import quote

import pytest
from eth_account import Account
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.chain import (
    ALREADY_RECORDED_SELECTOR,
    ARTIFACT_PATH,
    ChainError,
    ChainTimeout,
    InMemoryChain,
    Web3Chain,
    grams_to_ng,
)
from app.main import app, get_chain

client = TestClient(app)
SECRET = "secret-node-detail http://10.0.0.5:8545"


class FakeChain:
    description = "fake chain"

    def __init__(self, healthy: bool = True):
        self._healthy = healthy

    def healthy(self) -> bool:
        return self._healthy


@pytest.fixture
def fresh_state(monkeypatch):
    """get_chain is overridden in every other test; here we call the real one."""
    monkeypatch.setattr(main, "_chain", None)
    monkeypatch.setattr(main, "get_issuer_key", lambda: "0x" + "11" * 32)


def _deployments(monkeypatch, *chains):
    """Make Web3Chain.from_deployment return the given chains in order, counting calls."""
    queue = list(chains)
    calls = []

    def build(cls, key):
        calls.append(key)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(main.Web3Chain, "from_deployment", classmethod(build))
    return calls


def test_missing_deployment_is_a_503_that_says_how_to_fix_it(fresh_state, monkeypatch):
    _deployments(monkeypatch, FileNotFoundError("deployment.json"))
    with pytest.raises(HTTPException) as err:
        get_chain()
    assert err.value.status_code == 503 and "not configured" in err.value.detail


def test_corrupt_deployment_is_a_503_not_a_500(fresh_state, monkeypatch):
    _deployments(monkeypatch, json.JSONDecodeError("bad", "{", 0))
    with pytest.raises(HTTPException) as err:
        get_chain()
    assert err.value.status_code == 503 and "unreadable" in err.value.detail


def test_unreachable_chain_is_a_503(fresh_state, monkeypatch):
    _deployments(monkeypatch, FakeChain(healthy=False))
    with pytest.raises(HTTPException) as err:
        get_chain()
    assert err.value.status_code == 503 and "not reachable" in err.value.detail


def test_a_healthy_chain_is_built_once_and_reused(fresh_state, monkeypatch):
    chain = FakeChain()
    calls = _deployments(monkeypatch, chain)
    assert get_chain() is chain
    assert get_chain() is chain
    assert len(calls) == 1


def test_a_chain_that_stops_being_healthy_is_rebuilt_after_a_restart(fresh_state, monkeypatch):
    old, new = FakeChain(), FakeChain()
    calls = _deployments(monkeypatch, old, new)
    assert get_chain() is old
    old._healthy = False  # node restarted, contract gone
    assert get_chain() is new
    assert len(calls) == 2


def test_status_goes_red_when_the_chain_stops_being_healthy(fresh_state, monkeypatch):
    old = FakeChain()
    _deployments(monkeypatch, old, FakeChain(healthy=False))
    app.dependency_overrides.pop(main.get_chain_or_none)
    assert client.get("/v1/status").json()["chain"]["connected"] is True
    old._healthy = False
    assert client.get("/v1/status").json()["chain"]["connected"] is False


class _FailingWrites(InMemoryChain):
    def __init__(self, issuers, error):
        super().__init__(issuers)
        self._error = error

    def record(self, *args, **kwargs):
        raise self._error


class _FailingReads(InMemoryChain):
    def get_record(self, receipt_hash):
        raise ChainError(SECRET)

    def is_recorded(self, receipt_hash):
        raise ChainError(SECRET)

    def org_totals(self, org):
        raise ChainError(SECRET)


def _use(chain):
    app.dependency_overrides[get_chain] = lambda: chain


def test_a_chain_write_failure_is_a_502_without_internal_detail(issuer_key):
    _use(_FailingWrites({Account.from_key(issuer_key).address}, ChainError(SECRET)))
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "hi", "region": "eu-north"})
    assert response.status_code == 502
    assert "10.0.0.5" not in response.text and "secret" not in response.text
    assert "receipt" not in response.json()


def test_a_confirmation_timeout_is_a_504_that_warns_it_may_still_land(issuer_key):
    _use(_FailingWrites({Account.from_key(issuer_key).address}, ChainTimeout(SECRET)))
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "hi", "region": "eu-north"})
    assert response.status_code == 504
    assert "may still be recorded" in response.json()["detail"]
    assert "10.0.0.5" not in response.text


def test_verify_reports_a_chain_read_failure_as_502_not_invalid(issuer_key):
    _use(_FailingReads({Account.from_key(issuer_key).address}))
    response = client.post("/v1/verify", json={"receipt": {"a": 1}, "signature": "0x00"})
    assert response.status_code == 502
    assert "10.0.0.5" not in response.text


def test_totals_report_a_chain_read_failure_as_502(issuer_key):
    _use(_FailingReads(set()))
    response = client.get("/v1/orgs/acme/totals")
    assert response.status_code == 502 and "10.0.0.5" not in response.text


@pytest.mark.parametrize("org", ["a/b", "team/eu/north", "with space", "ünïcode-org"])
def test_unusual_organisation_names_can_be_recorded_and_read_back(org):
    response = client.post("/v1/chat", json={"org": org, "prompt": "hi", "region": "eu-north"})
    assert response.status_code == 200
    totals = client.get(f"/v1/orgs/{quote(org, safe='')}/totals")
    assert totals.status_code == 200
    assert totals.json()["org"] == org and totals.json()["receipts"] == 1


def test_receipt_bounds_are_recorded_as_whole_nanograms():
    assert grams_to_ng(2.98151e-06) == 2982
    assert grams_to_ng(0.4e-9) == 0
    assert grams_to_ng(0.6e-9) == 1


def test_the_already_recorded_selector_matches_the_contract():
    assert ALREADY_RECORDED_SELECTOR == "0x05a64c20"


@pytest.mark.skipif(not Path(ARTIFACT_PATH).exists(), reason="contract not built; run npx hardhat build in chain/")
def test_unreachable_node_gives_safe_errors_not_raw_exceptions():
    abi = json.loads(Path(ARTIFACT_PATH).read_text(encoding="utf-8"))["abi"]
    key = "0x" + bytes(Account.create().key).hex()
    chain = Web3Chain("http://127.0.0.1:9", "0x" + "00" * 19 + "01", abi, key)
    assert chain.healthy() is False
    with pytest.raises(ChainError) as read_error:
        chain.is_recorded(b"\x00" * 32)
    assert "127.0.0.1" not in str(read_error.value)
    with pytest.raises(ChainError) as write_error:
        chain.record(b"\x01" * 32, "acme", 0.0001, 0.0002)
    assert "127.0.0.1" not in str(write_error.value)
