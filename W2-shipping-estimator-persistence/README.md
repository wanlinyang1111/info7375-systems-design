# Week 2 — Shipping Estimator + Persistence

**Course:** INFO 7375 Systems Design in the Age of AI — Northeastern University  
**Stack:** AWS Lambda · API Gateway · DynamoDB · CloudWatch · AWS CDK (Python)  
**Patterns:** Strategy · Factory · Repository · Singleton  
**New concepts:** CAP theorem · Eventually consistent vs. strongly consistent reads

---

## Architecture

```
Client
  │
  ├─ POST /estimate   → save quote to DynamoDB, return quote_id
  └─ GET  /estimate/{quote_id} → retrieve saved quote
        │
        ▼
  API Gateway (HTTP API)
        │
        ▼
  Lambda: handler.py
        │
        ├─ ShippingStrategyFactory.get_strategy(speed_tier)   [Factory + Strategy]
        │       └─ strategy.estimate(weight_kg, zone)
        │
        └─ QuoteRepository.save(quote)                        [Repository + Singleton]
                └─ DynamoDB Table: quotes
```

## Design Patterns

### Repository
`QuoteRepository` is the only place in the codebase that touches `boto3` directly. The handler and the strategies never know DynamoDB exists — they only call `save()` / `get()`. This separation means persistence can be mocked cleanly in tests without patching AWS clients deep in business logic.

### Singleton
The DynamoDB resource and table objects are constructed **once at module level**, not inside a function. Every warm Lambda invocation reuses the same connection — a standard Lambda performance practice that is also a classic GoF Singleton, showing up because the platform rewards it, not because it was consciously "applied."

```python
# Singleton - constructed once per execution environment
_dynamodb = boto3.resource("dynamodb")
_table    = _dynamodb.Table(QUOTES_TABLE)
```

## CAP Theorem in Practice

W2 exposes the CAP theorem as a live, measurable tradeoff in the same DynamoDB table:

| Read path | ConsistentRead | Cost | Guarantee |
|-----------|---------------|------|-----------|
| `QuoteRepository.get()` | False (default) | 0.5 RCU | Eventually consistent — may return a replica not yet up-to-date |
| `QuoteRepository.get_strongly_consistent()` | True | 1.0 RCU | Strongly consistent — always reflects the most recent write |

`compare_read_consistency.py` writes a quote and immediately reads it back via both paths to measure the real latency and RCU cost difference on a live table (not visible in local mocks — moto does not model RCU accounting).

## Error Taxonomy

Three distinct failure codes, three distinct code paths:

| Status | Cause | Class |
|--------|-------|-------|
| 400 | `quote_id` is not a valid UUID format | `InvalidQuoteIdError` |
| 404 | Valid UUID but no matching record | `QuoteNotFoundError` |
| 500 | Uncaught exception (simulated via `__trigger_500__`) | `RuntimeError` |

Distinguishing 400 from 404 matters: a 400 means the caller sent garbage; a 404 means the caller sent a valid request that found nothing. Different causes, different retry strategies.

## Infrastructure

| Resource | Detail |
|----------|--------|
| Lambda | Python 3.10 · 256 MB |
| API Gateway | HTTP API (v2) · POST /estimate · GET /estimate/{quote_id} |
| DynamoDB | On-demand · PK: `quote_id` (String) |
| CloudWatch | Dashboard: `Info7375-Week2-ShippingEstimator` |

## Running Tests

```bash
cd W2-shipping-estimator-persistence
pip install pytest moto boto3
pytest test_shipping_estimator.py -v
```
