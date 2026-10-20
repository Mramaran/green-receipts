from datetime import datetime, timedelta, timezone

import pytest

from app import intensity


def test_every_region_has_24_hourly_values():
    data = intensity._load()
    for name, region in data["regions"].items():
        assert len(region["hourly_utc"]) == 24, name


def test_lookup_returns_value_for_hour_and_names_source():
    when = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)
    result = intensity.get_intensity("eu-north", when)
    assert result.hour_utc == 14
    assert result.g_per_kwh == 31.0
    assert result.source == "bundled-sample-v1"
    assert "NOT live" in result.source_note


def test_non_utc_time_is_converted_to_utc_hour():
    plus_five = timezone(timedelta(hours=5))
    when = datetime(2026, 10, 21, 19, 0, tzinfo=plus_five)  # 14:00 UTC
    assert intensity.get_intensity("eu-north", when).hour_utc == 14


def test_hour_rolls_over_at_the_utc_day_boundary():
    before = datetime(2026, 10, 21, 23, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 22, 0, 0, 0, tzinfo=timezone.utc)
    assert intensity.get_intensity("eu-north", before).hour_utc == 23
    assert intensity.get_intensity("eu-north", after).hour_utc == 0


def test_a_local_time_just_after_utc_midnight_uses_the_utc_hour():
    india = timezone(timedelta(hours=5, minutes=30))
    when = datetime(2026, 10, 22, 5, 29, tzinfo=india)  # 23:59 UTC the day before
    assert intensity.get_intensity("eu-north", when).hour_utc == 23


def test_unknown_region_raises_with_valid_list():
    when = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
    with pytest.raises(intensity.UnknownRegion) as err:
        intensity.get_intensity("mars-1", when)
    assert "eu-north" in str(err.value)
