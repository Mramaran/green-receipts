import pytest
from eth_account import Account

from app.chain import InMemoryChain
from app.main import app, get_chain, get_chain_or_none, get_issuer_key


@pytest.fixture
def issuer_key() -> str:
    return "0x" + bytes(Account.create().key).hex()


@pytest.fixture
def chain(issuer_key) -> InMemoryChain:
    return InMemoryChain(issuers={Account.from_key(issuer_key).address})


@pytest.fixture(autouse=True)
def wired_app(chain, issuer_key):
    app.dependency_overrides[get_chain] = lambda: chain
    app.dependency_overrides[get_chain_or_none] = lambda: chain
    app.dependency_overrides[get_issuer_key] = lambda: issuer_key
    yield
    app.dependency_overrides.clear()
