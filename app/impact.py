import threading
import warnings
from dataclasses import dataclass
from importlib.metadata import version

from ecologits.tracers.utils import llm_impacts

ESTIMATOR = f"ecologits {version('ecologits')}"

# warnings.catch_warnings changes global state, so only one estimate runs at a time.
_estimate_lock = threading.Lock()


@dataclass(frozen=True)
class EnergyEstimate:
    energy_kwh_min: float
    energy_kwh_max: float
    embodied_g_min: float
    embodied_g_max: float
    caveats: tuple[str, ...]


def _bounds(value) -> tuple[float, float]:
    """The estimator returns a min-max range for some figures and a single number for others."""
    if hasattr(value, "min") and hasattr(value, "max"):
        return float(value.min), float(value.max)
    return float(value), float(value)


def estimate_energy(provider: str, model: str, output_tokens: int, latency_s: float) -> EnergyEstimate:
    """Energy range for one request plus its embodied emissions range, from EcoLogits.

    Grid carbon intensity is applied separately so the receipt can use the
    hourly value for the declared region.
    """
    with _estimate_lock, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = llm_impacts(
            provider=provider,
            model_name=model,
            output_token_count=output_tokens,
            request_latency=latency_s,
        )
    energy_min, energy_max = _bounds(result.usage.energy.value)
    embodied_min_kg, embodied_max_kg = _bounds(result.embodied.gwp.value)
    caveats = tuple(w.message for w in (result.warnings or ()))
    return EnergyEstimate(
        energy_kwh_min=energy_min,
        energy_kwh_max=energy_max,
        embodied_g_min=embodied_min_kg * 1000.0,
        embodied_g_max=embodied_max_kg * 1000.0,
        caveats=caveats,
    )
