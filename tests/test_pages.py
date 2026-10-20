from pathlib import Path

from fastapi.testclient import TestClient

from app.main import STATIC_DIR, app, get_chain_or_none
from app.receipt import INTENSITY_UNCERTAINTY, LIMITS

client = TestClient(app)


def test_dashboard_and_methodology_pages_are_served():
    dashboard = client.get("/")
    methodology = client.get("/methodology")
    assert dashboard.status_code == 200 and "Green Receipts" in dashboard.text
    assert methodology.status_code == 200 and "Methodology" in methodology.text
    assert client.get("/static/app.css").status_code == 200


def test_pages_never_use_innerhtml():
    # Receipt fields such as the organisation name are user-controlled, so the pages
    # must insert text with textContent / text nodes only.
    for name in ("index.html", "methodology.html"):
        assert "innerHTML" not in Path(STATIC_DIR, name).read_text(encoding="utf-8"), name


def test_methodology_reports_the_constants_used_by_receipts():
    body = client.get("/v1/methodology").json()
    assert body["intensity_uncertainty_pct"] == int(INTENSITY_UNCERTAINTY * 100)
    assert body["accuracy_statement"] == "Estimate with a stated range. Not audit-grade."
    assert body["limits"] == list(LIMITS)
    assert body["estimator"].startswith("ecologits ")
    assert "NOT live" in body["grid"]["note"]


def test_methodology_lists_24_hourly_values_per_region():
    regions = client.get("/v1/methodology").json()["grid"]["regions"]
    assert "eu-north" in regions and "ap-south" in regions
    assert all(len(r["hourly_utc"]) == 24 for r in regions.values())


def test_status_reports_a_connected_chain():
    body = client.get("/v1/status").json()
    assert body["chain"]["connected"] is True
    assert body["chain"]["description"]


def test_status_reports_a_missing_chain_without_failing():
    app.dependency_overrides[get_chain_or_none] = lambda: None
    body = client.get("/v1/status")
    assert body.status_code == 200
    chain = body.json()["chain"]
    assert chain["connected"] is False
    assert chain["description"] is None and chain["contract"] is None and chain["rpc_url"] is None
