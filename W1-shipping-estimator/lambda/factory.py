"""
factory.py - the Factory pattern.

The Lambda handler needs a ShippingStrategy object for whatever
speed_tier the client asked for, but it should not need to know HOW
each strategy is constructed, or maintain its own if/elif chain
picking one. That is the Factory's one job: given a speed_tier
string, return the right Strategy object - or raise a clear error if
the string does not match anything supported.

Factory and Strategy are a common, natural pairing, not a
coincidence: the Factory decides WHICH strategy to build; the Strategy
decides WHAT to do once built. Two different responsibilities,
cleanly split.
"""

from strategy import StandardShipping, ExpressShipping, OvernightShipping


class UnsupportedSpeedTierError(ValueError):
    """
    Raised when the requested speed_tier has no matching Strategy.

    Deliberately a DIFFERENT error type than "malformed request data" -
    this is the Factory's own failure mode ("I don't have a Strategy
    for this"), not a data-validation failure. The handler treats the
    two differently even though both end up as a 400 response.
    """
    pass


_STRATEGIES = {
    "standard": StandardShipping,
    "express": ExpressShipping,
    "overnight": OvernightShipping,
}


class ShippingStrategyFactory:
    @staticmethod
    def get_strategy(speed_tier):
        strategy_class = _STRATEGIES.get(speed_tier)
        if strategy_class is None:
            raise UnsupportedSpeedTierError(
                "Unsupported speed_tier '{}'. Supported values: {}".format(
                    speed_tier, ", ".join(sorted(_STRATEGIES.keys()))
                )
            )
        return strategy_class()
