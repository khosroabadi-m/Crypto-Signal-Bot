"""v31_filter_runner.py — V3.1 scenario-isolated filter runner.

The ONLY scenario that can be filtered is the target scenario.
All other scenarios remain exactly V1.
"""

FILTERS = (
    "mtf",
    "adx_di",
    "atr_distance",
    "rsi",
    "exhaustion",
    "retest",
    "breakout",
)


def validate_filter_for_scenario(scenario, filter_name, scenario_config):
    """Check if a filter is allowed for a scenario."""
    if filter_name not in FILTERS:
        raise ValueError(f"Unknown filter: {filter_name}")
    allowed = scenario_config.get("allowed_filters", [])
    if filter_name not in allowed:
        return False
    return True


def should_take_signal(scenario, target_scenario, filter_name, scenario_config, filter_context):
    """Apply optional filter ONLY to the target scenario.

    If scenario != target_scenario, return True immediately (pure V1).
    This guarantees that testing S1+mtf cannot affect S2/S3/B1/B2.
    """
    if filter_name is None:
        return True

    # ISOLATION: only target scenario gets filtered
    if scenario != target_scenario:
        return True

    # Validate filter is allowed for this scenario
    if not validate_filter_for_scenario(scenario, filter_name, scenario_config):
        return True

    result_key = f"{filter_name}_ok"
    if result_key not in filter_context:
        raise KeyError(f"Missing filter result: {result_key}")

    return bool(filter_context[result_key])
