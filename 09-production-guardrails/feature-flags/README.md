# 🛡️ Feature Flags & Emergency Kill Switches

> **Domain 9: Production Guardrails & Governance ("Red Tape")** — PoC 9.1  
> **Real-World Analogs**: LaunchDarkly, Unleash, Flipt, Facebook Gatekeeper, AWS AppConfig  
> **Status**: Completed  
> **Difficulty**: Easy-Medium

---

## 1. Executive Context Header

In high-velocity distributed engineering, shipping code directly to 100% of production users in a single deployment is reckless. **Feature Flagging (Feature Toggles)** decouples **code deployment** (pushing bits to servers) from **feature release** (exposing behavior to users).

This proof-of-concept implements a high-throughput, thread-safe feature evaluation engine capable of:
1. **$O(1)$ Emergency Kill Switches**: Instantaneous circuit breakers to neutralize faulty features without redeploying code or restarting containers.
2. **Deterministic, Sticky, Monotonic Percentage Rollouts**: Hashing user IDs into integer buckets $[0, 99]$ so that expanding rollout from $15\% \to 30\%$ preserves previous cohorts without oscillation.
3. **Multi-Attribute Predicate Targeting**: Evaluating user identity, email domains, tenant tiers, and client SemVer versions.
4. **Multivariate Experimentation**: Weighted traffic allocation for A/B/n testing.
5. **Tamper-Evident Audit Trails**: Logging who modified flags, when, and why for SOC2/ISO-27001 compliance.

---

## 2. 30-Second Decision Matrix

| If your goal is to… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| **Mitigate bad releases in under 1 second** | **Emergency Kill Switch** | Bypasses all rules and short-circuits to safe fallback in $O(1)$ memory lookup | Requires maintaining well-tested fallback code paths |
| **Safely test in production without user exposure** | **Targeted Dark Launch** | Matches specific user IDs, internal emails, or tenant tiers before public rollout | Evaluates rules sequentially; rule order matters |
| **Ramp up traffic gradually (Canary testing)** | **Deterministic Hash Bucketing** | Guarantees user stickiness and monotonic inclusion across rollout increases | Requires consistent unique entity IDs (e.g. `user_id` or `device_id`) |
| **Compare multiple product hypotheses** | **Multivariate Experimentation** | Divides users across arbitrary variants ($A/B/n$) with custom traffic weights | Requires sufficient sample volume to achieve statistical significance |

---

## 3. Unified Interface & Invariants

```python
class FeatureFlagManager:
    def evaluate(
        self,
        flag_key: str,
        context: EvaluationContext,
        fallback_value: Any = False
    ) -> EvaluationResult:
        """Evaluates flag rules thread-safely against the context."""

    def emergency_kill_switch(
        self,
        flag_key: str,
        actor: str,
        reason: str
    ) -> bool:
        """Instantly disables flag in O(1) time without container restarts."""

    def set_percentage_rollout(
        self,
        flag_key: str,
        percentage: float,
        actor: str = "system"
    ) -> bool:
        """Adjusts the deterministic rollout allocation [0.0, 100.0]."""
```

### Safety & Concurrency Invariants
1. **The Monotonic Cohort Invariant**: If entity $u$ evaluates to `True` at rollout $P_1$, entity $u$ **must** evaluate to `True` at any higher rollout $P_2 > P_1$. Users must never experience "feature flickering".
2. **Deterministic Stickiness Invariant**: For any static flag configuration and context, $\text{evaluate}(flag, context)$ is purely functional and idempotent. Repeated calls never flip state.
3. **Fail-Closed Default**: If a flag key does not exist or internal exceptions occur, the engine fails closed to `fallback_value`, ensuring zero unhandled exceptions in the critical path.
4. **Thread Safety**: All reads and administrative updates are protected via re-entrant read/write locks (`threading.RLock`), supporting millions of concurrent evaluations per second in-process.

---

## 4. Algorithmic Breakdown with Math & Complexity

```
                             +-------------------------------+
                             |    Incoming Evaluation Req    |
                             | (flag_key, EvaluationContext) |
                             +---------------+---------------+
                                             |
                                             v
                             +-------------------------------+
                             |  1. Emergency Kill Switch?    | ---> YES ---> Return OFF Variation
                             +---------------+---------------+               (Reason: KILL_SWITCHED)
                                             | NO
                                             v
                             +-------------------------------+
                             |  2. Flag Globally Enabled?    | ---> NO  ---> Return OFF Variation
                             +---------------+---------------+               (Reason: DISABLED)
                                             | YES
                                             v
                             +-------------------------------+
                             |  3. Sequential Targeting Rules| ---> MATCH -> Return Rule Variation
                             +---------------+---------------+               (Reason: TARGETING_MATCH)
                                             | NO MATCH
                                             v
                             +-------------------------------+
                             |  4. Multivariate Variants?    | ---> MATCH -> Return Variant Variation
                             +---------------+---------------+               (Reason: MULTIVARIATE_BUCKET)
                                             | NONE
                                             v
                             +-------------------------------+
                             |  5. Percentage Rollout Hash   | ---> < P% --> Return Rollout Variation
                             |     bucket = Hash(key:id) %100|               (Reason: PERCENTAGE_ROLLOUT)
                             +---------------+---------------+
                                             | >= P%
                                             v
                             +-------------------------------+
                             |  6. Default Fallback Value    | ------------> Return OFF Variation
                             +-------------------------------+               (Reason: DEFAULT_FALLBACK)
```

### 1. Deterministic Hash Bucketing Formula
To partition users into buckets without storing user lists in memory, we compute a 32-bit integer from the SHA-256 digest of the concatenated salt:

$$\text{digest} = \text{SHA-256}(\text{flag\_key} \parallel \text{":"} \parallel \text{entity\_id})$$

$$\text{bucket} = \text{int}(\text{digest}[0:8], 16) \pmod{100}$$

$$\text{is\_active} = \begin{cases} 
\text{True} & \text{if } \text{bucket} < P \\
\text{False} & \text{if } \text{bucket} \ge P 
\end{cases}$$

Where $P \in [0.0, 100.0]$ is the rollout percentage.

**Proof of Monotonicity**:  
Let $u$ be an entity with bucket integer $b = \text{bucket}(k, u)$.  
If $u$ is active at $P_1$, then $b < P_1$.  
For any $P_2 > P_1$, by transitivity $b < P_1 < P_2 \implies b < P_2$.  
Therefore, $u$ remains active for all $P_2 > P_1$. $\blacksquare$

### 2. Time & Space Complexity

| Operation | Time Complexity | Space Complexity | Notes |
| :--- | :---: | :---: | :--- |
| **Emergency Kill Switch** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Atomic boolean assignment |
| **Percentage Rollout Eval** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Fixed-length 32-bit SHA-256 computation |
| **Targeting Rules Eval** | $\mathcal{O}(R)$ | $\mathcal{O}(1)$ | $R$ is number of rules for that flag |
| **Multivariate Assignment** | $\mathcal{O}(V)$ | $\mathcal{O}(1)$ | $V$ is number of variations (typically $V \le 5$) |
| **Memory Storage** | $\mathcal{O}(F \cdot R)$ | $\mathcal{O}(F \cdot R)$ | $F$ flags, $R$ average rules per flag |

---

## 5. Multi-Dimensional Trade-off Matrix

| Architecture | Evaluation Latency | Blast Radius Mitigation | Operational Overhead | Memory Footprint |
| :--- | :---: | :---: | :---: | :---: |
| **In-Memory Local Engine (This PoC)** | **$< 1 \, \mu\text{s}$** (Sub-microsecond) | Immediate (via local lock or event bus) | Minimal (Embedded library) | Negligible ($\sim \text{few MBs}$) |
| **Remote HTTP / REST Evaluation** | $20 - 100 \, \text{ms}$ (Network hop) | High (Centralized control) | Heavy (Network dependency on hot path) | Zero local memory |
| **Edge CDN / Cloudflare Workers** | $2 - 5 \, \text{ms}$ | High (Global propagation) | Moderate (Edge KV sync latency) | Low |
| **Database-Backed Flags** | $5 - 15 \, \text{ms}$ | High (Single source of truth) | High (Risk of DB connection pool starvation) | Low |

---

## 6. Runnable Lab & Telemetry Interpretation Guide

### Running Unit Tests
```bash
.\.venv\Scripts\pytest.exe 09-production-guardrails/feature-flags/ -v
```

### Running Interactive Simulation
```bash
.\.venv\Scripts\python.exe 09-production-guardrails/feature-flags/simulate.py
```

### Interpreting Console Output
```text
User ID      | Hash Bucket  | 0% Stage | 15% Stage | 40% Stage | 75% Stage | 100% Stage
---------------------------------------------------------------------------
user_05      | bucket 05  | [  --  ] | [ACTIVE] | [ACTIVE] | [ACTIVE] | [ACTIVE]
user_09      | bucket 38  | [  --  ] | [  --  ] | [ACTIVE] | [ACTIVE] | [ACTIVE]
user_04      | bucket 75  | [  --  ] | [  --  ] | [  --  ] | [  --  ] | [ACTIVE]
```
- **Bucket 05**: Admitted at the $15\%$ stage because $5 < 15$. Notice it remains `[ACTIVE]` continuously through $40\%$, $75\%$, and $100\%$.
- **Bucket 38**: Skipped at $15\%$ ($38 \ge 15$), admitted at $40\%$ ($38 < 40$), and remains active thereafter.
- **Zero Oscillations**: Validates that no user is bounced between active and inactive states.

---

## 7. Bridging to Distributed Architecture

In a distributed cluster of 500 microservice pods, having each pod maintain a disconnected local flag state creates configuration drift.

```
                         +-----------------------------+
                         | Admin UI / Release Pipeline |
                         +--------------+--------------+
                                        | (Update / Kill Switch)
                                        v
                         +-----------------------------+
                         | Central Flag Config Store   |
                         | (Postgres / DynamoDB)       |
                         +--------------+--------------+
                                        |
                   +--------------------+--------------------+
                   |                                         |
                   v (Push Event)                            v (Push Event)
        +---------------------+                   +---------------------+
        | Redis Pub/Sub / SSE |                   | Kafka Config Topic  |
        +----------+----------+                   +----------+----------+
                   |                                         |
         +---------+---------+                     +---------+---------+
         |                   |                     |                   |
         v                   v                     v                   v
   +-----------+       +-----------+         +-----------+       +-----------+
   | Pod 1 Mem |       | Pod 2 Mem |         | Pod 3 Mem |       | Pod N Mem |
   | Flag Cache|       | Flag Cache|         | Flag Cache|       | Flag Cache|
   +-----------+       +-----------+         +-----------+       +-----------+
```

### Distributed Sync Protocol (Streaming Relay)
1. **Local Evaluation, Remote Synchronization**: Evaluations **must always** be executed in-process memory ($< 1\,\mu\text{s}$). Never make an RPC or Redis network call in the request hot path to check a flag.
2. **Server-Sent Events (SSE) / Redis Pub/Sub**: When an operator trips an emergency kill switch, an invalidation message is broadcast over persistent SSE streams or Redis Pub/Sub channels to all running pods in under $100\,\text{ms}$.
3. **Fail-Closed vs. Fail-Open Policy**:
   - **Financial/Security Flags** (e.g., new payment route, crypto processor): **Fail-Closed** (`fallback=False`). If flag state is missing or corrupted, disable feature.
   - **Resilience / Circuit Breaker Flags** (e.g., load shedding, cache bypass): **Fail-Open** (`fallback=True`). If degraded, keep traffic flowing.
4. **LaunchDarkly Relay Proxy Pattern**: For massive clusters (10,000+ pods), microservices connect to localized edge proxies within the same VPC subnet, reducing outbound connection overhead while preserving sub-second invalidations.
