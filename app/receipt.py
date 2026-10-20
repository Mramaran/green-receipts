import uuid
from datetime import datetime, timezone

from app.impact import ESTIMATOR, EnergyEstimate
from app.intensity import Intensity
from app.providers import Completion

SCHEMA = "green-receipts/1"
METHOD_ID = "green-receipts-method-v1"
# Assumed uncertainty of the bundled sample intensity values. It widens the
# emissions range and is stated on every receipt.
INTENSITY_UNCERTAINTY = 0.15
FORMULA = (
    "gCO2e = energy_kWh x grid_intensity_gCO2e_per_kWh + embodied_gCO2e. "
    "Energy is a min-max range from the estimator. Intensity is the hourly value for the "
    "declared region, widened by the stated uncertainty."
)
ACCURACY_STATEMENT = "Estimate with a stated range. Not audit-grade."
REGION_NOTE = "Declared by the caller. The provider's actual data-centre region is not observed."

LIMITS = (
    "The answering model is a mock. Impact is estimated as if a reference model served the request, "
    "with simulated latency.",
    "Grid intensity comes from bundled illustrative hourly profiles, not live measurements.",
    "The region is declared by the caller. The provider's actual data-centre region is not observed.",
    "Energy is estimated from output tokens and latency by an open-source estimator. It is not metered.",
    "The estimator reports reduced precision for models whose architecture is not public.",
    "The 15% grid-intensity uncertainty is an assumption, not a measured error.",
    "Anyone who can reach the service can create receipts for any organisation name. "
    "There is no authentication in this prototype.",
    "Totals are recorded on the chain in whole nanograms, so a receipt under half a nanogram records as zero.",
)


def _sig(value: float) -> float:
    return float(f"{value:.6g}")


def build_receipt(
    *,
    org: str,
    completion: Completion,
    region: str,
    when: datetime,
    intensity: Intensity,
    energy: EnergyEstimate,
) -> dict:
    low = intensity.g_per_kwh * (1 - INTENSITY_UNCERTAINTY)
    high = intensity.g_per_kwh * (1 + INTENSITY_UNCERTAINTY)
    return {
        "schema": SCHEMA,
        "receipt_id": str(uuid.uuid4()),
        "org": org,
        "issued_at": when.astimezone(timezone.utc).isoformat(),
        "request": {
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "latency_s": completion.latency_s,
            "model_reference": f"{completion.reference_provider}/{completion.reference_model}",
            "is_mock": completion.is_mock,
        },
        "region": {"declared": region, "note": REGION_NOTE},
        "grid": {
            "hour_utc": intensity.hour_utc,
            "intensity_g_per_kwh": intensity.g_per_kwh,
            "source": intensity.source,
            "source_note": intensity.source_note,
        },
        "energy_kwh": {
            "min": _sig(energy.energy_kwh_min),
            "max": _sig(energy.energy_kwh_max),
        },
        "emissions_gco2e": {
            "min": _sig(energy.energy_kwh_min * low + energy.embodied_g_min),
            "max": _sig(energy.energy_kwh_max * high + energy.embodied_g_max),
            "embodied_g_min": _sig(energy.embodied_g_min),
            "embodied_g_max": _sig(energy.embodied_g_max),
        },
        "method": {
            "id": METHOD_ID,
            "estimator": ESTIMATOR,
            "formula": FORMULA,
            "intensity_uncertainty_pct": round(INTENSITY_UNCERTAINTY * 100),
            "caveats": list(energy.caveats),
            "accuracy_statement": ACCURACY_STATEMENT,
        },
    }
