from datetime import datetime, timezone

import pytest

from app.impact import EnergyEstimate
from app.intensity import Intensity
from app.providers import Completion
from app.receipt import ACCURACY_STATEMENT, INTENSITY_UNCERTAINTY, build_receipt

WHEN = datetime(2026, 10, 21, 6, 30, tzinfo=timezone.utc)
COMPLETION = Completion(
    text="x",
    input_tokens=10,
    output_tokens=40,
    latency_s=1.1,
    reference_provider="openai",
    reference_model="gpt-4o-mini",
    is_mock=True,
)


def _intensity(g_per_kwh: float = 100.0) -> Intensity:
    return Intensity(region="eu-north", hour_utc=6, g_per_kwh=g_per_kwh, source="bundled-sample-v1", source_note="note")


def _energy(**changes) -> EnergyEstimate:
    values = dict(
        energy_kwh_min=2e-06,
        energy_kwh_max=4e-06,
        embodied_g_min=1e-04,
        embodied_g_max=3e-04,
        caveats=("estimator caveat",),
    )
    values.update(changes)
    return EnergyEstimate(**values)


def _build(intensity=None, energy=None) -> dict:
    return build_receipt(
        org="acme",
        completion=COMPLETION,
        region="eu-north",
        when=WHEN,
        intensity=intensity or _intensity(),
        energy=energy or _energy(),
    )


def test_bounds_apply_the_intensity_uncertainty_and_embodied_range():
    emissions = _build()["emissions_gco2e"]
    # min: lowest energy x intensity 15% lower + lowest embodied
    assert emissions["min"] == pytest.approx(2e-06 * 100 * 0.85 + 1e-04, rel=1e-5)
    # max: highest energy x intensity 15% higher + highest embodied
    assert emissions["max"] == pytest.approx(4e-06 * 100 * 1.15 + 3e-04, rel=1e-5)


def test_embodied_spread_widens_the_range():
    emissions = _build()["emissions_gco2e"]
    operational_spread = 4e-06 * 100 * 1.15 - 2e-06 * 100 * 0.85
    embodied_spread = 3e-04 - 1e-04
    assert emissions["max"] - emissions["min"] == pytest.approx(operational_spread + embodied_spread, rel=1e-4)


def test_min_never_exceeds_max_across_intensities():
    for g in (0.0, 5.0, 29.0, 720.0, 5000.0):
        emissions = _build(intensity=_intensity(g))["emissions_gco2e"]
        assert emissions["min"] <= emissions["max"], g


def test_receipt_reports_both_embodied_bounds():
    emissions = _build()["emissions_gco2e"]
    assert emissions["embodied_g_min"] == pytest.approx(1e-04)
    assert emissions["embodied_g_max"] == pytest.approx(3e-04)
    assert "embodied_g" not in emissions


def test_values_are_rounded_to_six_significant_figures():
    receipt = _build(energy=_energy(energy_kwh_min=1.23456789e-06, energy_kwh_max=1.98765432e-06))
    assert receipt["energy_kwh"] == {"min": 1.23457e-06, "max": 1.98765e-06}


def test_energy_is_reported_in_kwh_not_milliwatt_hours():
    receipt = _build()
    assert receipt["energy_kwh"]["min"] == 2e-06
    assert receipt["energy_kwh"]["max"] == 4e-06


def test_method_block_states_the_assumptions():
    method = _build()["method"]
    assert method["intensity_uncertainty_pct"] == round(INTENSITY_UNCERTAINTY * 100) == 15
    assert method["accuracy_statement"] == ACCURACY_STATEMENT
    assert method["caveats"] == ["estimator caveat"]


def test_each_receipt_gets_a_new_id_and_a_utc_timestamp():
    first, second = _build(), _build()
    assert first["receipt_id"] != second["receipt_id"]
    assert first["issued_at"] == "2026-10-21T06:30:00+00:00"
