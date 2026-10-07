"""
test_shipping_estimator.py

Tests the actual business logic - Strategy and Factory - directly,
with zero AWS involvement. No moto, no mocking, no Lambda event
shapes to construct. This is the payoff of keeping the handler thin:
everything that actually matters is plain Python, testable in
milliseconds, with no AWS account or network access required.

Run with:
    pytest test_shipping_estimator.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambda"))

import pytest
from factory import ShippingStrategyFactory, UnsupportedSpeedTierError
from strategy import StandardShipping, ExpressShipping, OvernightShipping


def test_factory_returns_correct_strategy_type():
    assert isinstance(ShippingStrategyFactory.get_strategy("standard"), StandardShipping)
    assert isinstance(ShippingStrategyFactory.get_strategy("express"), ExpressShipping)
    assert isinstance(ShippingStrategyFactory.get_strategy("overnight"), OvernightShipping)


def test_factory_rejects_unknown_tier():
    with pytest.raises(UnsupportedSpeedTierError):
        ShippingStrategyFactory.get_strategy("teleport")


def test_standard_shipping_local_zone():
    strategy = StandardShipping()
    result = strategy.estimate(weight_kg=10, destination_zone="local")
    # base 5.00 + 10*0.50 = 10.00, * 1.0 zone multiplier = 10.00
    assert result["cost"] == 10.00
    assert result["estimated_days"] == 5


def test_express_shipping_regional_zone():
    strategy = ExpressShipping()
    result = strategy.estimate(weight_kg=4, destination_zone="regional")
    # base 15.00 + 4*1.00 = 19.00, * 1.5 = 28.50
    assert result["cost"] == 28.50
    assert result["estimated_days"] == 2


def test_overnight_shipping_international_zone():
    strategy = OvernightShipping()
    result = strategy.estimate(weight_kg=2, destination_zone="international")
    # base 40.00 + 2*2.00 = 44.00, * 3.0 = 132.00
    assert result["cost"] == 132.00
    assert result["estimated_days"] == 1


def test_same_weight_different_tiers_cost_differently():
    # A concrete illustration of WHY Strategy exists: the exact same
    # input produces genuinely different behavior depending only on
    # which Strategy object handles it.
    standard = StandardShipping().estimate(5, "local")
    overnight = OvernightShipping().estimate(5, "local")
    assert overnight["cost"] > standard["cost"]
    assert overnight["estimated_days"] < standard["estimated_days"]
