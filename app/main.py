import logging
import os
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from eth_account import Account
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import intensity
from app.chain import ChainClient, ChainError, ChainTimeout, Web3Chain
from app.impact import ESTIMATOR, estimate_energy
from app.keys import load_or_create_issuer_key
from app.providers import MockProvider
from app.receipt import (
    ACCURACY_STATEMENT,
    FORMULA,
    INTENSITY_UNCERTAINTY,
    LIMITS,
    METHOD_ID,
    REGION_NOTE,
    build_receipt,
)
from app.signing import receipt_hash, sign_hash
from app.verify import verify_receipt

log = logging.getLogger("green_receipts")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Green Receipts")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
provider = MockProvider()
_chain: Web3Chain | None = None
_chain_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def get_issuer_key() -> str:
    # Hosted: the key comes from the GR_ISSUER_KEY secret. Local: the .issuer_key file.
    return os.environ.get("GR_ISSUER_KEY", "").strip() or load_or_create_issuer_key()


def get_chain() -> ChainClient:
    """The live chain client. Re-checked on every call, and rebuilt from deployment.json
    if the node was restarted or the contract was redeployed since the last request."""
    global _chain
    with _chain_lock:
        if _chain is not None and _chain.healthy():
            return _chain
        _chain = None
        try:
            candidate = Web3Chain.from_deployment(get_issuer_key())
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail="chain not configured: start a local node and run scripts/deploy_local.py",
            ) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=503, detail="chain deployment files are unreadable: redeploy the contract"
            ) from exc
        if not candidate.healthy():
            raise HTTPException(
                status_code=503,
                detail="chain is not reachable or the contract is not deployed: "
                "start the node and run scripts/deploy_local.py",
            )
        _chain = candidate
        return _chain


def get_chain_or_none() -> ChainClient | None:
    try:
        return get_chain()
    except HTTPException:
        return None


class ChatRequest(BaseModel):
    org: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=4000)
    region: str = Field(min_length=1, max_length=32)


class VerifyRequest(BaseModel):
    receipt: dict
    signature: str = Field(min_length=1, max_length=200)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/methodology", include_in_schema=False)
def methodology_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "methodology.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/status")
def status(chain: ChainClient | None = Depends(get_chain_or_none)) -> dict:
    return {
        "chain": {
            "connected": chain is not None,
            "description": chain.description if chain else None,
            "contract": getattr(chain, "address", None),
            "rpc_url": getattr(chain, "rpc_url", None),
        },
        "estimator": ESTIMATOR,
    }


@app.get("/v1/methodology")
def methodology() -> dict:
    grid = intensity.profiles()
    return {
        "method_id": METHOD_ID,
        "estimator": ESTIMATOR,
        "formula": FORMULA,
        "intensity_uncertainty_pct": round(INTENSITY_UNCERTAINTY * 100),
        "accuracy_statement": ACCURACY_STATEMENT,
        "region_note": REGION_NOTE,
        "reference_model": f"{provider.reference_provider}/{provider.reference_model}",
        "limits": list(LIMITS),
        "grid": {
            "source": grid["source_id"],
            "unit": grid["unit"],
            "note": grid["note"],
            "regions": {
                name: {"zone": region["zone"], "hourly_utc": region["hourly_utc"]}
                for name, region in grid["regions"].items()
            },
        },
    }


@app.get("/regions")
def list_regions() -> dict:
    return {"regions": intensity.regions()}


@app.post("/v1/chat")
def chat(
    req: ChatRequest,
    chain: ChainClient = Depends(get_chain),
    issuer_key: str = Depends(get_issuer_key),
) -> dict:
    when = _now()
    try:
        grid = intensity.get_intensity(req.region, when)
    except intensity.UnknownRegion as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    completion = provider.complete(req.prompt)
    energy = estimate_energy(
        completion.reference_provider,
        completion.reference_model,
        completion.output_tokens,
        completion.latency_s,
    )
    receipt = build_receipt(
        org=req.org,
        completion=completion,
        region=req.region,
        when=when,
        intensity=grid,
        energy=energy,
    )
    digest = receipt_hash(receipt)
    signature = sign_hash(digest, issuer_key)
    try:
        tx_hash = chain.record(
            digest, req.org, receipt["emissions_gco2e"]["min"], receipt["emissions_gco2e"]["max"]
        )
    except ChainTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail="the chain did not confirm in time; the receipt may still be recorded",
        ) from exc
    except ChainError as exc:
        log.warning("recording receipt failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not record the receipt on the chain") from exc
    return {
        "answer": completion.text,
        "receipt": receipt,
        "proof": {
            "receipt_hash": "0x" + digest.hex(),
            "signature": signature,
            "issuer": Account.from_key(issuer_key).address,
            "tx_hash": tx_hash,
            "chain": chain.description,
            "contract": getattr(chain, "address", None),
            "rpc_url": getattr(chain, "rpc_url", None),
        },
    }


@app.post("/v1/verify")
def verify(req: VerifyRequest, chain: ChainClient = Depends(get_chain)) -> dict:
    try:
        return verify_receipt(req.receipt, req.signature, chain)
    except ChainError as exc:
        log.warning("verification could not read the chain: %s", exc)
        raise HTTPException(status_code=502, detail="could not read the chain to verify this receipt") from exc


@app.get("/v1/orgs/{org:path}/totals")
def org_totals(org: str, chain: ChainClient = Depends(get_chain)) -> dict:
    try:
        totals = chain.org_totals(org)
    except ChainError as exc:
        log.warning("reading totals failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not read the totals from the chain") from exc
    return {
        "org": org,
        "receipts": totals.receipts,
        "gco2e_min": totals.gco2e_min,
        "gco2e_max": totals.gco2e_max,
    }
