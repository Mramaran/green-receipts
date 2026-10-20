import json

from fastapi.testclient import TestClient

from app.main import app
from app.verify import verify_receipt

client = TestClient(app)


def _as_the_browser_sees_it(value):
    """JSON.parse turns 31.0 into 31, so integer-valued floats come back as ints."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _as_the_browser_sees_it(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_the_browser_sees_it(item) for item in value]
    return value


def _issue() -> dict:
    response = client.post("/v1/chat", json={"org": "acme", "prompt": "hello", "region": "uk-south"})
    assert response.status_code == 200
    return response.json()


def test_receipt_still_verifies_after_the_browser_reformats_it():
    body = _issue()
    shown = json.dumps(body["receipt"], indent=2)  # what the textarea holds
    edited = _as_the_browser_sees_it(json.loads(shown))
    result = client.post("/v1/verify", json={"receipt": edited, "signature": body["proof"]["signature"]}).json()
    assert result["valid"] is True, result


def test_key_order_in_the_pasted_receipt_does_not_matter():
    body = _issue()
    reversed_order = dict(reversed(list(body["receipt"].items())))
    result = client.post("/v1/verify", json={"receipt": reversed_order, "signature": body["proof"]["signature"]}).json()
    assert result["valid"] is True


def test_a_deeply_nested_receipt_is_rejected_cleanly(chain):
    nested: dict = {}
    cursor = nested
    for _ in range(5000):
        cursor["a"] = {}
        cursor = cursor["a"]
    result = verify_receipt(nested, "0x00", chain)
    assert result["valid"] is False
    assert result["checks"][0]["name"] == "receipt_well_formed"
