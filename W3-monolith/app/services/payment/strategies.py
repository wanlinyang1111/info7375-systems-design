"""
Payment Strategy pattern
========================
Context:  PaymentProcessor
Strategy: PaymentStrategy (abstract base)
Concrete: StripeStrategy, StubStrategy

KEY DESIGN DECISION: PaymentProcessor never references Stripe or Stub
directly. It only calls process(). Adding a new payment provider means:
  1. Create a new class that inherits PaymentStrategy
  2. Register it in PAYMENT_STRATEGIES below
  3. Zero changes to PaymentProcessor or any route

This is the seam that becomes a microservice boundary in Week 4.
Students who build this cleanly can extract it with a network call
and no interface changes.
"""
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)


# -- Result dataclass -------------------------------------------------------
@dataclass
class PaymentResult:
    success:          bool
    transaction_id:   str | None
    provider:         str
    amount_charged:   float
    duration_ms:      float
    error:            str | None = None


# -- Abstract Strategy ------------------------------------------------------
class PaymentStrategy(ABC):
    @abstractmethod
    def process(self, amount: float, card_token: str, order_id: str) -> PaymentResult:
        """Charge the card and return a PaymentResult. Must be idempotent on order_id."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


# -- Concrete: Stripe -------------------------------------------------------
class StripeStrategy(PaymentStrategy):
    """
    Real Stripe implementation. Requires STRIPE_SECRET_KEY env var.
    In the lab we use the stub unless students add their own test key.
    """
    provider_name = "stripe"

    def __init__(self):
        import os
        self._api_key = os.getenv("STRIPE_SECRET_KEY")
        if not self._api_key:
            raise EnvironmentError("STRIPE_SECRET_KEY not set")

    def process(self, amount: float, card_token: str, order_id: str) -> PaymentResult:
        start = time.time()
        try:
            import stripe
            stripe.api_key = self._api_key
            charge = stripe.Charge.create(
                amount=int(amount * 100),   # Stripe uses cents
                currency="usd",
                source=card_token,
                idempotency_key=order_id,   # exactly-once charge
                metadata={"order_id": order_id},
            )
            return PaymentResult(
                success=True,
                transaction_id=charge.id,
                provider=self.provider_name,
                amount_charged=amount,
                duration_ms=round((time.time() - start) * 1000, 1),
            )
        except Exception as exc:
            log.error("stripe_charge_failed", extra={"order_id": order_id, "error": str(exc)})
            return PaymentResult(
                success=False,
                transaction_id=None,
                provider=self.provider_name,
                amount_charged=0,
                duration_ms=round((time.time() - start) * 1000, 1),
                error=str(exc),
            )


# -- Concrete: Stub (default for lab) --------------------------------------
class StubStrategy(PaymentStrategy):
    """
    Always succeeds. Used in lab so students don't need a real payment key.
    Simulates ~50ms network latency so load tests reflect realistic timing.

    BREAK PHASE VARIANT: set PAYMENT_STUB_FAIL_RATE=0.1 to inject 10% failures.
    Watch error rate climb in CloudWatch without touching any other code.
    """
    provider_name = "stub"

    def __init__(self):
        import os
        self._fail_rate = float(os.getenv("PAYMENT_STUB_FAIL_RATE", "0"))
        self._latency_ms = float(os.getenv("PAYMENT_STUB_LATENCY_MS", "50"))

    def process(self, amount: float, card_token: str, order_id: str) -> PaymentResult:
        import random
        import uuid
        start = time.time()
        time.sleep(self._latency_ms / 1000)

        if random.random() < self._fail_rate:
            return PaymentResult(
                success=False,
                transaction_id=None,
                provider=self.provider_name,
                amount_charged=0,
                duration_ms=round((time.time() - start) * 1000, 1),
                error="Stub: injected failure",
            )

        return PaymentResult(
            success=True,
            transaction_id=f"stub_{uuid.uuid4().hex[:16]}",
            provider=self.provider_name,
            amount_charged=amount,
            duration_ms=round((time.time() - start) * 1000, 1),
        )


# -- Context ----------------------------------------------------------------
class PaymentProcessor:
    """
    Context class. Holds a reference to whichever PaymentStrategy is
    configured. Never imports Stripe or Stub directly — only the interface.
    """
    def __init__(self, strategy: PaymentStrategy):
        self._strategy = strategy

    def charge(self, amount: float, card_token: str, order_id: str) -> PaymentResult:
        log.info(
            "payment_attempt",
            extra={"provider": self._strategy.provider_name,
                   "order_id": order_id, "amount": amount},
        )
        result = self._strategy.process(amount, card_token, order_id)
        log.info(
            "payment_result",
            extra={"provider": self._strategy.provider_name,
                   "success": result.success,
                   "duration_ms": result.duration_ms,
                   "order_id": order_id},
        )
        return result


# -- Factory: resolve strategy from config ---------------------------------
# Adding a new provider = one line here + a new class above.
PAYMENT_STRATEGIES: dict[str, type[PaymentStrategy]] = {
    "stripe": StripeStrategy,
    "stub":   StubStrategy,
}


def get_payment_processor() -> PaymentProcessor:
    """FastAPI dependency — returns a configured PaymentProcessor."""
    import os
    provider = os.getenv("PAYMENT_PROVIDER", "stub")
    strategy_cls = PAYMENT_STRATEGIES.get(provider)
    if not strategy_cls:
        raise ValueError(f"Unknown payment provider: {provider}. "
                         f"Valid options: {list(PAYMENT_STRATEGIES)}")
    return PaymentProcessor(strategy_cls())
