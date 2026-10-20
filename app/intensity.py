import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "intensity_hourly.json"


class UnknownRegion(ValueError):
    pass


@dataclass(frozen=True)
class Intensity:
    region: str
    hour_utc: int
    g_per_kwh: float
    source: str
    source_note: str


@lru_cache(maxsize=1)
def _load() -> dict:
    with DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def profiles() -> dict:
    """The bundled hourly profiles with their source note, for the methodology page."""
    return _load()


def regions() -> list[str]:
    return sorted(_load()["regions"])


def get_intensity(region: str, when: datetime) -> Intensity:
    data = _load()
    if region not in data["regions"]:
        raise UnknownRegion(f"unknown region {region!r}; valid regions: {', '.join(regions())}")
    hour = when.astimezone(timezone.utc).hour
    return Intensity(
        region=region,
        hour_utc=hour,
        g_per_kwh=float(data["regions"][region]["hourly_utc"][hour]),
        source=data["source_id"],
        source_note=data["note"],
    )
