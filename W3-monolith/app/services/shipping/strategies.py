"""
Shipping Strategy pattern
=========================
Context:  ShippingCalculator
Strategy: ShippingStrategy (abstract base)
Concrete: FlatRateStrategy, WeightBasedStrategy

Second Strategy seam — deliberately different domain to show
the pattern generalises. In Week 4, this becomes the Shipping microservice.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ShippingQuote:
    cost:   float
    method: str
    eta_days: int


class ShippingStrategy(ABC):
    @abstractmethod
    def calculate(self, weight_kg: float, item_count: int) -> ShippingQuote:
        ...

    @property
    @abstractmethod
    def method_name(self) -> str:
        ...


class FlatRateStrategy(ShippingStrategy):
    """$5.99 regardless of weight. Simple. Predictable. Often wrong."""
    method_name = "flat_rate"
    RATE = 5.99

    def calculate(self, weight_kg: float, item_count: int) -> ShippingQuote:
        return ShippingQuote(cost=self.RATE, method=self.method_name, eta_days=5)


class WeightBasedStrategy(ShippingStrategy):
    """$2.99 base + $1.50/kg. Fairer for heavy orders."""
    method_name = "weight_based"
    BASE = 2.99
    PER_KG = 1.50

    def calculate(self, weight_kg: float, item_count: int) -> ShippingQuote:
        cost = self.BASE + (weight_kg * self.PER_KG)
        return ShippingQuote(
            cost=round(cost, 2),
            method=self.method_name,
            eta_days=3,
        )


class ExpressStrategy(ShippingStrategy):
    """$19.99 flat, next-day. Added without touching ShippingCalculator."""
    method_name = "express"
    RATE = 19.99

    def calculate(self, weight_kg: float, item_count: int) -> ShippingQuote:
        return ShippingQuote(cost=self.RATE, method=self.method_name, eta_days=1)


# -- Context ----------------------------------------------------------------
class ShippingCalculator:
    def __init__(self, strategy: ShippingStrategy):
        self._strategy = strategy

    def quote(self, weight_kg: float, item_count: int) -> ShippingQuote:
        return self._strategy.calculate(weight_kg, item_count)


# -- Factory ----------------------------------------------------------------
SHIPPING_STRATEGIES: dict[str, type[ShippingStrategy]] = {
    "flat_rate":    FlatRateStrategy,
    "weight_based": WeightBasedStrategy,
    "express":      ExpressStrategy,
}


def get_shipping_calculator(method: str = "flat_rate") -> ShippingCalculator:
    strategy_cls = SHIPPING_STRATEGIES.get(method)
    if not strategy_cls:
        raise ValueError(f"Unknown shipping method: {method}. "
                         f"Valid: {list(SHIPPING_STRATEGIES)}")
    return ShippingCalculator(strategy_cls())
