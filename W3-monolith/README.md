# Week 3 — Monolith on EC2 + RDS

**Course:** INFO 7375 Systems Design in the Age of AI — Northeastern University  
**Stack:** FastAPI · EC2 t3.medium · RDS PostgreSQL · ALB · AWS CDK (Python)  
**Patterns:** Strategy (payment & shipping) · Repository · Facade  
**Load testing:** Artillery (10→100 arr/s ramp, 9-minute profile)

---

## Architecture

```
Client
  │
  └─ ALB (Application Load Balancer)
        │
        └─ EC2 t3.medium
              │  uvicorn (2 workers)
              │
              ├─ GET  /products/           → RDS SELECT
              ├─ POST /orders/             → RDS INSERT + payment + shipping logic
              └─ GET  /health
                    │
                    └─ RDS PostgreSQL (shared connection pool, pool_size=20)
```

Everything — products, orders, payment logic, shipping logic — runs in a single process sharing one database connection pool.

## Design Patterns

### Strategy (Payment & Shipping)
`app/services/payment/strategies.py` and `app/services/shipping/strategies.py` carry over the Strategy pattern from W1/W2. Each payment provider (stub, stripe) and each shipping method (flat_rate, weight_based) is a separate Strategy with a common interface.

### Repository
`app/repository/database.py` owns all SQLAlchemy session management. Routes never touch the engine directly.

## Break Scenarios

| Scenario | Injection | Effect on `/products/` | Effect on `/orders/` |
|----------|-----------|----------------------|---------------------|
| A — Connection pool exhaustion | `pool_size=2` | Degraded — shared pool starved | Timeout |
| B — Slow query (no index) | Remove index on `orders.user_id` | Degraded — same pool | Very slow |
| C — Payment latency | `time.sleep(2)` in payment stub | Degraded — same process | +2s per order |
| D — Payment crash | `raise Exception` in payment stub | **Entire process crashes** | Down |

**Key finding:** In a monolith, all endpoints share one process and one connection pool. A crash anywhere brings down everything. A slow query in `orders` starves product catalog queries because both compete for the same 20 connections.

## Baseline Results (AWS)

| Metric | Value |
|--------|-------|
| p50 latency | 21 ms |
| p99 latency | 100 ms |
| Peak throughput | ~100 req/s |
| Socket timeouts | < 1% |
