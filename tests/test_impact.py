from app.impact import estimate_energy


def test_energy_range_is_positive_and_ordered():
    result = estimate_energy("openai", "gpt-4o-mini", 200, 3.0)
    assert 0 < result.energy_kwh_min <= result.energy_kwh_max
    assert 0 <= result.embodied_g_min <= result.embodied_g_max


def test_more_output_tokens_means_more_energy():
    short = estimate_energy("openai", "gpt-4o-mini", 50, 1.0)
    long = estimate_energy("openai", "gpt-4o-mini", 400, 6.0)
    assert long.energy_kwh_max > short.energy_kwh_max
