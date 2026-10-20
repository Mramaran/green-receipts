import uuid

import pytest
from eth_account import Account
from eth_utils import keccak

from app.chain import DEPLOYMENT_PATH, AlreadyRecorded, Web3Chain, grams_to_ng, org_id
from app.keys import load_or_create_issuer_key


@pytest.fixture
def real_chain():
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json; start a local node and run scripts/deploy_local.py")
    key = load_or_create_issuer_key()
    chain = Web3Chain.from_deployment(key)
    if not chain.is_connected():
        pytest.skip("local chain node is not reachable")
    return chain, key


def test_record_and_read_back_on_the_real_contract(real_chain):
    chain, key = real_chain
    digest = keccak(f"it-{uuid.uuid4()}".encode())
    org = f"it-org-{uuid.uuid4().hex[:8]}"

    assert chain.has_issuer_role(Account.from_key(key).address)
    assert chain.is_recorded(digest) is False

    chain.record(digest, org, 0.000244, 0.000288)

    record = chain.get_record(digest)
    assert record is not None
    assert record.issuer == Account.from_key(key).address
    assert record.org_id == org_id(org)
    assert (record.gco2e_min_ng, record.gco2e_max_ng) == (grams_to_ng(0.000244), grams_to_ng(0.000288))
    assert record.recorded_at > 0
    assert chain.get_record(keccak(b"never recorded " + digest)) is None

    assert chain.is_recorded(digest) is True
    totals = chain.org_totals(org)
    assert totals.receipts == 1
    assert totals.gco2e_min == pytest.approx(0.000244)
    assert totals.gco2e_max == pytest.approx(0.000288)

    with pytest.raises(AlreadyRecorded):
        chain.record(digest, org, 0.000244, 0.000288)
