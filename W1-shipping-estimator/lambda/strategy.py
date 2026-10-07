"""
strategy.py - the Strategy pattern.

Each shipping speed tier is a variation on the same operation: given a
package weight and a destination zone, produce a cost estimate and a
delivery time. The formula differs per tier, but the SHAPE of the
operation - what it takes in, what it returns - is identical. That is
exactly the situation Strategy exists for: one common interface, one
small class per variation, and calling code that never needs an
if/elif chain to know which one it is holding.

Note: each concrete class below fully implements its own estimate(),
even though the three bodies look similar. That repetition is
deliberate, not an oversight - if the shared formula were pulled up
into the base class with only the constants varying per subclass,
that would start to blend Strategy with a second pattern (Template
Method). Keeping each Strategy self-contained keeps this first
exposure to one pattern at a time.
"""

from abc import ABC, abstractmethod

# SPEC: destination zone multipliers. Deliberately a flat multiplier
# per zone, not a real distance/geography model - the lesson here is
# the pattern, not the pricing.
ZONE_MULTIPLIERS = {
    "local": 1.0,
    "regional": 1.5,
    "international": 3.0,
}


class ShippingStrategy(ABC):
    """The common interface every speed tier must implement."""

    @abstractmethod
    def estimate(self, weight_kg, destination_zone):
        """Returns {"cost": float, "estimated_days": int}."""
        raise NotImplementedError


class StandardShipping(ShippingStrategy):
    BASE_RATE = 5.00
    PER_KG_RATE = 0.50
    ESTIMATED_DAYS = 5

    def estimate(self, weight_kg, destination_zone):
        zone_multiplier = ZONE_MULTIPLIERS[destination_zone]
        cost = (self.BASE_RATE + weight_kg * self.PER_KG_RATE) * zone_multiplier
        return {"cost": round(cost, 2), "estimated_days": self.ESTIMATED_DAYS}


class ExpressShipping(ShippingStrategy):
    BASE_RATE = 15.00
    PER_KG_RATE = 1.00
    ESTIMATED_DAYS = 2

    def estimate(self, weight_kg, destination_zone):
        zone_multiplier = ZONE_MULTIPLIERS[destination_zone]
        cost = (self.BASE_RATE + weight_kg * self.PER_KG_RATE) * zone_multiplier
        return {"cost": round(cost, 2), "estimated_days": self.ESTIMATED_DAYS}


class OvernightShipping(ShippingStrategy):
    BASE_RATE = 40.00
    PER_KG_RATE = 2.00
    ESTIMATED_DAYS = 1

    def estimate(self, weight_kg, destination_zone):
        zone_multiplier = ZONE_MULTIPLIERS[destination_zone]
        cost = (self.BASE_RATE + weight_kg * self.PER_KG_RATE) * zone_multiplier
        return {"cost": round(cost, 2), "estimated_days": self.ESTIMATED_DAYS}
