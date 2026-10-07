# Week 1 — Shipping Cost Estimator

**Course:** INFO 7375 Systems Design in the Age of AI — Northeastern University  
**Stack:** AWS Lambda · API Gateway (HTTP API) · CloudWatch · AWS CDK (Python)  
**Patterns:** Strategy · Factory

---

## Architecture

```
Client
  │
  └─ POST /estimate
        │
        ▼
  API Gateway (HTTP API)
        │
        ▼
  Lambda: handler.py
        │
        ├─ validates input (weight_kg, destination_zone)
        │
        └─ ShippingStrategyFactory.get_strategy(speed_tier)
                │
                ├─ "standard"  → StandardShipping.estimate()
                ├─ "express"   → ExpressShipping.estimate()
                └─ "overnight" → OvernightShipping.estimate()
```

## Design Patterns

### Strategy
Each shipping speed tier is a variation on the same operation — given `weight_kg` and `destination_zone`, return `cost` and `estimated_days`. The formula differs per tier, but the interface is identical. One abstract base class, one concrete class per tier, no `if/elif` chain in the caller.

| Tier | Base rate | Per-kg | Days |
|------|-----------|--------|------|
| standard | $5.00 | $0.50/kg | 5 |
| express | $15.00 | $1.00/kg | 2 |
| overnight | $40.00 | $2.00/kg | 1 |

Zone multipliers: `local` 1.0× · `regional` 1.5× · `international` 3.0×

### Factory
`ShippingStrategyFactory.get_strategy(speed_tier)` selects and instantiates the correct Strategy. The handler never knows which concrete class it receives — only that it has an `estimate()` method. This separates *which strategy to build* (Factory) from *what to do once built* (Strategy).

## Key Design Decision

The Lambda handler is deliberately thin — its only job is translating API Gateway's event shape into a plain Python function call. All business logic lives in `strategy.py` and `factory.py`, which can be tested with zero AWS involvement.

## Request / Response

```bash
curl -X POST $API_ENDPOINT \
  -H "Content-Type: application/json" \
  -d '{"weight_kg": 2.5, "destination_zone": "regional", "speed_tier": "express"}'
```

```json
{
  "speed_tier": "express",
  "destination_zone": "regional",
  "weight_kg": 2.5,
  "cost": 26.25,
  "estimated_days": 2
}
```

## Infrastructure

| Resource | Detail |
|----------|--------|
| Lambda | Python 3.10 · 128 MB · 10s timeout |
| API Gateway | HTTP API (v2) · single route POST /estimate |
| CloudWatch | Dashboard: `Info7375-Week1-ShippingEstimator` |

## Running Tests

```bash
cd W1-shipping-estimator
pip install pytest
pytest test_shipping_estimator.py -v
```

Tests cover all three tiers across all three zones — no AWS credentials required.
