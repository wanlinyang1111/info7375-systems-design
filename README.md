# INFO 7375 — Systems Design in the Age of AI

**Northeastern University · Fall 2026**  
**Instructor:** Dino Konstantopoulos

Labs exploring how system architecture evolves under real production constraints — design patterns, persistence, load testing, failure injection, and cloud deployment with AWS CDK.

---

## Architecture Evolution

| Week | System | Stack | Design Patterns | Baseline p99 |
|------|--------|-------|----------------|-------------|
| [W1](./W1-shipping-estimator/) | Shipping Cost Estimator | Lambda · API Gateway | Strategy · Factory | — |
| [W2](./W2-shipping-estimator-persistence/) | Estimator + Persistence | Lambda · API Gateway · DynamoDB | + Repository · Singleton | — |
| [W3](./W3-monolith/) | E-commerce Monolith | FastAPI · EC2 · RDS · ALB | + Facade | **100 ms** |
| [W4](./W4-microservices/) | Microservices | FastAPI × 3 · ECS Fargate · RDS · X-Ray | — | **5,711 ms** |

---

## The Central Question

Each week builds on the same domain (shipping / e-commerce) and asks a progressively harder question:

- **W1–W2:** How do you separate business logic from infrastructure so both can change independently?
- **W3:** What breaks first when a single process handles everything under load?
- **W4:** Does decomposing into services actually contain failures — and what does it cost?

---

## Key Findings

### W3 → W4: Blast Radius Containment Has a Price

The W4 microservices baseline p99 is **57× higher** than W3 (5,711 ms vs 100 ms). Two synchronous HTTP hops per order + 0.25 vCPU Fargate tasks at 100 arr/s explain the gap.

The payoff: in every W4 break scenario, `GET /products/` stayed fast (53–140 ms) while the injected failure degraded only the service on the broken call path. In W3, any process-level failure took down all endpoints simultaneously.

### Timeout Configuration Is an Architectural Decision

In W4 Scenario A (PaymentService 2,000 ms latency), OrderService held connections open waiting indefinitely. Adding `PAYMENT_TIMEOUT_S=1.0` (Scenario B) converted silent hangs into explicit HTTP 504 responses in 1,152 ms — freeing connections and giving callers actionable errors.

### Fast Failures Can Lower p99 — Without Meaning the System Is Healthier

W4 Scenario D (`PAYMENT_STUB_FAIL_RATE=0.5`) produced a p99 of 5,272 ms — *lower* than the healthy baseline (5,711 ms). HTTP 402 failures resolved in ~170 ms vs ~2,000 ms for successful orders, pulling the distribution down. 1,184 orders failed. Lower p99 ≠ healthier system.

---

## Tech Stack

| Category | Tools |
|----------|-------|
| Language | Python 3.10–3.12 |
| Web framework | FastAPI (W3, W4) |
| IaC | AWS CDK (Python) |
| Compute | AWS Lambda (W1–W2) · EC2 t3.medium (W3) · ECS Fargate (W4) |
| Database | DynamoDB (W2) · RDS PostgreSQL (W3–W4) |
| Load testing | Artillery |
| Tracing | AWS X-Ray (W4) |
| Monitoring | CloudWatch |

---

## Design Patterns Covered

| Pattern | Category | First appears |
|---------|----------|--------------|
| Strategy | Behavioral | W1 |
| Factory | Creational | W1 |
| Repository | Architectural | W2 |
| Singleton | Creational | W2 |
| Facade | Structural | W3 |
| Observer | Behavioral | W5 |
| Command | Behavioral | W5 |
