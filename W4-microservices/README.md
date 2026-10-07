# Week 4 — Microservices on ECS Fargate

**Course:** INFO 7375 Systems Design in the Age of AI — Northeastern University  
**Stack:** FastAPI × 3 · ECS Fargate · ALB (public + internal) · RDS PostgreSQL · AWS X-Ray · AWS CDK (Python)  
**Load testing:** Artillery (10→100 arr/s ramp, 9-minute profile — identical to W3)

---

## Architecture

```
Client
  │
  └─ Public ALB
        │
        └─ OrderService  (port 8000, 0.25 vCPU / 512 MB)
              │
              ├─ GET  /products/    → RDS (no downstream calls)
              ├─ POST /orders/      → Internal ALB → PaymentService + ShippingService → RDS
              └─ GET  /health
                    │
              Internal ALB
              ├─ PaymentService   (port 8001, 0.25 vCPU / 512 MB)
              └─ ShippingService  (port 8002, 0.25 vCPU / 512 MB)

Each service: its own ECS task, its own connection pool, its own CloudWatch log group.
Distributed tracing: AWS X-Ray daemon sidecar in every task.
```

## W3 vs W4 Comparison

| Dimension | W3 Monolith | W4 Microservices |
|-----------|------------|-----------------|
| Compute | 1 EC2, 2 uvicorn workers | 3 ECS Fargate tasks |
| DB connections | Shared pool (pool_size=20) | Isolated pool per service |
| Payment call | In-process function (~0 ms) | HTTP over internal ALB (+50 ms min) |
| Shipping call | In-process function (~0 ms) | HTTP over internal ALB (+10 ms min) |
| Blast radius | Crash anywhere = crash everywhere | PaymentService crash leaves `/products/` running |
| Baseline p99 | **100 ms** | **5,711 ms** (network hop cost at scale) |

## Break Scenarios

| Scenario | Env var | `/products/` | `/orders/` | p99 |
|----------|---------|-------------|-----------|-----|
| Baseline | — | — | — | 5,711 ms |
| A — Payment latency | `PAYMENT_STUB_LATENCY_MS=2000` | 80 ms ✅ | 2,175 ms | 7,866 ms |
| B — Payment timeout | `PAYMENT_TIMEOUT_S=1.0` | 140 ms ✅ | 504 in 1,152 ms | 7,710 ms |
| C — Shipping latency | `SHIPPING_LATENCY_MS=3000` | 90 ms ✅ | 3,187 ms | 8,025 ms |
| D — Payment fail rate | `PAYMENT_STUB_FAIL_RATE=0.5` | 53 ms ✅ | 3/5 fail (402) | 5,272 ms |

**Key finding — blast radius containment:** In every scenario, `GET /products/` remained fast and unaffected. A failure injected into PaymentService or ShippingService propagated only along the call path that involves those services. Sibling services continued running independently.

**Cost of containment:** The microservices baseline p99 is 57× higher than the monolith (5,711 ms vs 100 ms) because every `POST /orders/` now makes two synchronous HTTP calls over the internal ALB, and 0.25 vCPU Fargate tasks saturate under 100 arr/s load.

## Failure Modes Observed

- **Scenario B:** Without a timeout, a slow downstream holds caller connections indefinitely. With `PAYMENT_TIMEOUT_S=1.0`, OrderService aborts after 1s and returns HTTP 504, freeing connections immediately.
- **Scenario D:** Fast HTTP 402 failures resolve in ~170 ms vs ~2,000 ms for successful orders. The p99 *drops* compared to baseline (5,272 ms vs 5,711 ms) because failed payments reduce the slow-request queue. Lower p99 ≠ healthier system — 1,184 orders failed.
- **Connection pools:** Each service owns its pool. A blocked ShippingService connection cannot starve product catalog queries — the key isolation that W3 lacked.

## Infrastructure

| Resource | Detail |
|----------|--------|
| ECS Cluster | `info7375-week4` |
| Fargate tasks | 3 × (0.25 vCPU, 512 MB) |
| Public ALB | OrderService: `/products/`, `/orders/`, `/health` |
| Internal ALB | PaymentService (8001), ShippingService (8002) |
| RDS | PostgreSQL db.t3.micro, shared across services |
| X-Ray | Daemon sidecar in every task — traces cross-service request paths |
| Dashboard | CloudWatch: `INFO7375-Week4-Microservices` |
