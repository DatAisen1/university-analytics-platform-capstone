"""Unit tests for the derived admissions funnel (P0 #15)."""

from __future__ import annotations

import pytest

from data_generator.generators.generate_admissions import derive_funnel_counts


def test_funnel_ordering_always_holds():
    """applicants >= accepted >= enrolled_freshmen for a wide sweep of
    realistic rate combinations -- the core guarantee this module exists
    to provide."""
    for enrolled in (0, 1, 5, 50, 500):
        for yield_rate in (0.55, 0.7, 0.85, 1.0):
            for acceptance_rate in (0.55, 0.7, 0.85, 1.0):
                result = derive_funnel_counts(enrolled, yield_rate, acceptance_rate)
                assert result["applicants"] >= result["accepted"] >= result["enrolled_freshmen"]


def test_zero_enrolled_yields_zero_funnel():
    result = derive_funnel_counts(0, 0.7, 0.7)
    assert result == {"applicants": 0, "accepted": 0, "enrolled_freshmen": 0}


def test_lower_yield_rate_requires_more_accepted():
    high_yield = derive_funnel_counts(100, yield_rate=0.85, acceptance_rate=0.7)
    low_yield = derive_funnel_counts(100, yield_rate=0.55, acceptance_rate=0.7)
    assert low_yield["accepted"] > high_yield["accepted"]


@pytest.mark.parametrize("bad_rate", [0.0, -0.1, 1.1])
def test_rates_outside_valid_range_raise(bad_rate):
    with pytest.raises(ValueError):
        derive_funnel_counts(10, yield_rate=bad_rate, acceptance_rate=0.7)
    with pytest.raises(ValueError):
        derive_funnel_counts(10, yield_rate=0.7, acceptance_rate=bad_rate)