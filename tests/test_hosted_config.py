import json

from app import main
from app.chain import PACKAGED_ARTIFACT_PATH, Web3Chain

SECRET_RPC = "https://eth-sepolia.example.invalid/v2/SECRETKEY"
PUBLIC_RPC = "https://public.example.invalid"
ADDRESS = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
KEY = "0x" + "22" * 32


def test_packaged_artifact_has_abi_and_bytecode():
    data = json.loads(PACKAGED_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert data["abi"] and data["bytecode"].startswith("0x")


def test_env_config_never_exposes_the_private_rpc_url(monkeypatch):
    monkeypatch.setenv("GR_RPC_URL", SECRET_RPC)
    monkeypatch.setenv("GR_PUBLIC_RPC_URL", PUBLIC_RPC)
    monkeypatch.setenv("GR_CONTRACT_ADDRESS", ADDRESS)
    chain = Web3Chain.from_deployment(KEY)
    assert chain.address == ADDRESS
    assert chain.rpc_url == PUBLIC_RPC
    assert "SECRETKEY" not in chain.description


def test_issuer_key_comes_from_env(monkeypatch):
    main.get_issuer_key.cache_clear()
    monkeypatch.setenv("GR_ISSUER_KEY", KEY)
    assert main.get_issuer_key() == KEY
    main.get_issuer_key.cache_clear()
