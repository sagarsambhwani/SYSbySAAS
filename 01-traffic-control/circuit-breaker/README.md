# ⚡ PoC 1.2: Circuit Breaker & Fallback Pattern

> **Domain:** Traffic Control & System Resilience  
> **Status:** ✅ Completed  
> **Real-World Analogs:** Netflix Hystrix, Resilience4j, Envoy Service Mesh, Multi-LLM AI Gateways (Portkey, LiteLLM)

---

## 🎯 What You Will Learn

In distributed systems, a completely dead dependency rarely takes down your application because it fails immediately in $1\text{ms}$. **A slow, degraded dependency is what destroys systems** by exhausting thread pools and socket connections.

This PoC implements a thread-safe, sliding-window **Circuit Breaker** state machine (`CLOSED` $\to$ `OPEN` $\to$ `HALF_OPEN`). It demonstrates:
1. **Cascading Failure Prevention**: Quarantining unhealthy services before thread starvation brings down your entire application.
2. **Sub-Millisecond Fail-Fast**: Returning immediate fallback responses in $0\text{ms}$ instead of waiting on $30\text{s}$ network timeouts.
3. **Canary Self-Healing**: Testing the waters with small trial probe requests without thundering-herd slamming a recovering dependency.
4. **AI Multi-Model Fallback Routing**: Seamlessly routing LLM prompts from a failing primary model (e.g., GPT-4o) to a fallback model (e.g., Claude 3.5 Sonnet / local vLLM).

---

## ⏱️ 30-Second Decision Matrix

| If your problem is… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| Downstream dependency is slow or throwing systemic 503s | **Circuit Breaker** | Fails fast immediately; stops hammering the dead service | Requires defining fallback logic and recovery parameters |
| Random network blip or dropped packet | **Retry with Jitter** | Transient blips self-heal in a single retry | Toxic if used on a dying dependency (creates retry storms) |
| A dependency takes too long and freezes threads | **Timeout** | Sets a hard ceiling on latency | Leaves callers with errors unless paired with a fallback |
| Users / bots are flooding your API beyond capacity | **Rate Limiter** | Throttles incoming client requests | Protects ingress, but does not protect against downstream failures |

---

## 🔌 The Public Request Contract

Every protected call is wrapped using the same contract:

```python
result = circuit_breaker.call(
    downstream_service.generate,
    prompt="Summarize this text",
    fallback=fallback_model_handler,
)
```

You can also protect functions via decorator:
```python
@circuit_breaker(fallback=my_fallback_function)
def call_primary_api():
    ...
```

### Safety & Concurrency Invariants
- **Thread Safety**: All state transitions and sliding window metric appends are guarded by `threading.Lock()`.
- **Clock Drift Immunity**: State durations and recovery timers use `time.monotonic()` to prevent system clock or NTP adjustments from corrupting state transitions.

---

## 🔬 The 3-State Finite State Machine (FSM)

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                          │
        ▼                                                          │
   [ CLOSED ]  ───(Failure rate >= 50%)──►   [  OPEN  ]            │ (All canary
(Normal Traffic)                           (Fail FAST! 0ms)        │  trials pass)
        ▲                                          │               │
        │                                  (After cooldown,        │
        │                                   e.g. 5.0 seconds)      │
        │                                          │               │
        │                                          ▼               │
        └────────(Any trial fails)─────────  [ HALF-OPEN ] ────────┘
                                            (Canary Probes)
```

### 1. `CLOSED` (Normal Flow)
- Requests flow directly to the downstream dependency.
- Recent outcomes are recorded in a sliding ring buffer of size $W$.
- Tripping condition:
  $$\text{Evaluated if: } \text{Total Calls in Window} \ge \text{Min Throughput}$$
  $$\text{Failure Rate} = \frac{\text{Failures}}{\text{Window Length}} \ge \text{Threshold} \implies \text{Transition to OPEN}$$

### 2. `OPEN` (Fail-Fast Quarantine)
- The downstream dependency is marked unhealthy.
- **Zero requests touch the downstream dependency.**
- If a fallback is configured, it executes immediately ($< 0.1\text{ms}$).
- If no fallback is provided, `CircuitBreakerOpenException` is raised instantly.
- Benefits: Frees up caller threads instantly and allows the downstream dependency breathing room to recover.

### 3. `HALF_OPEN` (Canary Trial)
- Once `recovery_timeout` has elapsed, the breaker transitions to `HALF_OPEN`.
- Admits a controlled trial batch of $K$ requests (e.g. $K = 3$).
- **Success Criteria**: If all $K$ requests succeed, the service has self-healed $\to$ Transition to `CLOSED`.
- **Failure Criteria**: If *any* trial request fails, the dependency is still sick $\to$ Snap back to `OPEN` and reset the cooldown timer.

---

## 📊 Comparison Matrix

| Dimension | `CLOSED` State | `OPEN` State | `HALF_OPEN` State |
| :--- | :---: | :---: | :---: |
| **Traffic to Dependency** | 100% | 0% (Quarantined) | Canary trials only (e.g. 3 reqs) |
| **Caller Latency** | Full network latency | $< 0.1\text{ms}$ (Instant) | Full network latency (trial only) |
| **Worker Threads** | Consumed during call | Freed immediately | Consumed for canary only |
| **Failure Reaction** | Appends failure to window | Rejects / invokes fallback | Snaps back to `OPEN` immediately |
| **Success Reaction** | Appends success to window | N/A | Increments canary counter towards `CLOSED` |

---

## 🧪 Runnable Lab & Simulation Guide

Ensure your virtual environment is active:

```powershell
# Run unit & concurrency test suite
.venv\Scripts\python.exe -m pytest 01-traffic-control\circuit-breaker\test_circuit_breaker.py -v

# Run the live interactive terminal simulation
.venv\Scripts\python.exe 01-traffic-control\circuit-breaker\simulate.py
```

### Simulation Walkthrough
The simulator evaluates an AI gateway across four real-world phases:

1. **Phase 1: Healthy Operations**: Primary model (GPT-4o) responds at baseline ~30ms latency. Breaker stays `CLOSED`.
2. **Phase 2: Downstream Outage Storm**: Primary model throws 503s. The failure rate crosses 50% threshold $\to$ Breaker trips `OPEN`.
3. **Phase 3: Fail-Fast & Fallback Routing**: Zero calls are sent to GPT-4o. Latency drops to **0.0ms**. Every request is seamlessly served by the fallback model (Claude 3.5 Sonnet) with zero thread hanging.
4. **Phase 4: Canary Probing & Healing**: Cooldown expires. 3 canary probes are admitted in `HALF_OPEN`. All 3 succeed $\to$ Breaker automatically heals back to `CLOSED`.

---

## 🌐 Bridging to Distributed Production Architecture

In enterprise architectures, Circuit Breakers operate at two distinct layers:

### 1. In-Process vs. Service Mesh (Envoy / Istio)
- **In-Process Breaker (Application Layer)**: Ideal when application context is required to execute intelligent semantic fallbacks (e.g., switching LLM providers, serving stale cache data, or degraded UI components).
- **Service Mesh Breaker (Envoy Proxy Sidecar)**: Ideal for infrastructure-level protection (TCP connection pool overflow, HTTP/2 stream limits, outlier ejection) independent of programming languages.

### 2. Distributed State vs. Local In-Memory Breakers
- **Local Breakers (Per-Pod)**: Recommended for most microservices. Fast, zero-network overhead, completely immune to external Redis outages.
- **Centralized Breakers (Shared Redis State)**: Used when strict cluster-wide error budgets must be enforced across thousands of ephemeral serverless functions (AWS Lambda).

### 3. AI Multi-Model Routing Pattern
```
                                Incoming Prompt
                                       │
                              [ Circuit Breaker ]
                               ├── CLOSED ──► Primary: OpenAI GPT-4o
                               └── OPEN   ──► Fallback: Anthropic Claude / Local vLLM
```
When OpenAI or Anthropic suffers an outage or severe latency spike, the circuit breaker protects your users from experiencing infinite spinner wheels or 504 gateway timeouts by immediately shifting traffic to secondary providers.
