# 🚦 PoC 1.1: Distributed Rate Limiting Engines

> **Domain:** Traffic Control & Reliability  
> **Status:** ✅ Completed  
> **Real-World Systems:** Stripe API, AWS API Gateway, Cloudflare Edge, Nginx

---

## 📌 Problem Overview

In large-scale distributed architectures, rate limiting is the first line of defense against:
1. **Resource Starvation / DoS**: Preventing rogue clients or misconfigured microservices from exhausting database connection pools and CPU threads.
2. **Cascading Service Outages**: Flattening sudden flash traffic crowds before downstream services get overwhelmed.
3. **Multi-Tenant Fairness**: Guaranteeing tier-based SLAs (e.g., Free vs Pro API tiers).

---

## 🔬 Algorithmic Deep Dive

This PoC implements and benchmarks four rate limiting algorithms:

```
                    ┌──────────────────────────────────────────────┐
                    │            INCOMING HTTP REQUESTS            │
                    └──────────────────────┬───────────────────────┘
                                           │
         ┌──────────────────┬──────────────┴─────┬──────────────────┐
         │                  │                    │                  │
         ▼                  ▼                    ▼                  ▼
┌──────────────────┐┌──────────────────┐┌──────────────────┐┌──────────────────┐
│   TOKEN BUCKET   ││   LEAKY BUCKET   ││SLIDING WINDOW LOG││  SLIDING WINDOW  │
│                  ││                  ││                  ││     COUNTER      │
│ Refills tokens   ││ Constant rate    ││ Exact timestamp  ││ Weighted average │
│ at rate 'r'      ││ output queue     ││ history in deque ││ of rolling windows│
│ (Allows Bursts)  ││ (Traffic Shaper) ││ (High Memory O(N)││ (Low Memory O(1))│
└──────────────────┘└──────────────────┘└──────────────────┘└──────────────────┘
```

### 1. Token Bucket
- **Mechanism**: A bucket with capacity $C$ continuously accumulates tokens at rate $r$ tokens/second. When a request arrives, $1$ (or $k$) tokens are deducted. If tokens $< k$, the request is dropped (HTTP 429).
- **Refill Math**:
  $$\text{tokens} = \min\Big(C, \text{tokens} + (\text{now} - \text{last\_refill}) \times r\Big)$$
- **Pros**: $O(1)$ memory and CPU; gracefully handles short legitimate bursts up to capacity $C$.
- **Used by**: Stripe, AWS API Gateway, GitHub REST API.

---

### 2. Leaky Bucket
- **Mechanism**: Requests enter a FIFO queue (capacity $C$). Requests leak out and are processed at a strictly constant rate. If incoming traffic exceeds queue capacity, new requests overflow and drop.
- **Leak Math**:
  $$\text{water\_level} = \max\Big(0, \text{water\_level} - (\text{now} - \text{last\_leak}) \times \text{leak\_rate}\Big)$$
- **Pros**: Eliminates bursts completely, ensuring a perfectly smooth, constant downstream consumption rate.
- **Used by**: Nginx `limit_req`, Shopify API, network packet traffic shaping.

---

### 3. Sliding Window Log
- **Mechanism**: Maintains a timestamped sorted log of all accepted requests. On every incoming request, timestamps older than $(\text{now} - \text{window\_duration})$ are pruned. If the remaining count $< \text{limit}$, request is accepted.
- **Pros**: 100% accurate sliding rate limit with zero boundary burst exploits.
- **Cons**: High memory consumption ($O(N)$ per client), where $N$ is the number of requests in the window.
- **Used by**: Critical financial transaction endpoints, strict authentication failure rate limiting.

---

### 4. Sliding Window Counter (Cloudflare Hybrid)
- **Mechanism**: Divides time into discrete fixed windows. When evaluating a request at time $t$ in the current window, it calculates a weighted sum combining the previous window's total and the current window's accumulated count.
- **Estimation Formula**:
  $$\text{weight} = \frac{\text{window\_size} - (\text{now} - \text{current\_window\_start})}{\text{window\_size}}$$
  $$\text{estimated\_requests} = (\text{previous\_window\_count} \times \text{weight}) + \text{current\_window\_count}$$
- **Pros**: $O(1)$ memory (only 2 integer counters), smooths out fixed-window boundary spikes.
- **Used by**: Cloudflare edge DDoS mitigation.

---

## 📊 Comparison Matrix

| Algorithm | Time Complexity | Space Complexity | Supports Bursts? | Memory Footprint | Edge Case / Drawback |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Token Bucket** | $O(1)$ | $O(1)$ | ✅ Yes (up to $C$) | Low ($\approx$ 16 bytes) | Can cause downstream spikes if burst capacity is high |
| **Leaky Bucket** | $O(1)$ | $O(1)$ | ❌ No (smooths) | Low ($\approx$ 16 bytes) | Bursts suffer latency or packet drop |
| **Sliding Window Log** | $O(M)$ eviction | $O(N)$ | ❌ No | High ($\propto$ req count) | Memory bloat under high throughput attacks |
| **Sliding Window Counter** | $O(1)$ | $O(1)$ | ⚠️ Approximate | Low ($\approx$ 24 bytes) | Assumes uniform request distribution in previous window |

---

## 🌐 Scaling to Distributed Systems (Redis Pattern)

In a multi-server setup, in-memory limiters don't share state across multiple API gateway nodes. Production systems use **Redis** with **Atomic Lua Scripts**:

### Token Bucket in Redis Lua (Atomic Execution)
```lua
-- KEYS[1]: client rate limit key (e.g., "ratelimit:user:123")
-- ARGV[1]: capacity
-- ARGV[2]: refill_rate_per_sec
-- ARGV[3]: current_timestamp
-- ARGV[4]: tokens_requested

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

-- Replenish tokens
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + (elapsed * refill_rate))

if tokens >= requested then
    tokens = tokens - requested
    redis.call("HMSET", key, "tokens", tokens, "last_refill", now)
    redis.call("EXPIRE", key, math.ceil(capacity / refill_rate))
    return 1 -- ALLOWED
else
    return 0 -- REJECTED (HTTP 429)
end
```

---

## 🚀 Running Tests and Simulation

Ensure your virtual environment is active:

```bash
# Run unit & concurrency tests
pytest 01-traffic-control/rate-limiters/test_rate_limiters.py -v

# Run multi-threaded stress and telemetry simulation
python 01-traffic-control/rate-limiters/simulate.py
```

### Standard HTTP 429 Response Headers
When rate limiting in production, always return standard informative headers:
```http
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1709280000
Retry-After: 30
Content-Type: application/json

{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Please retry after 30 seconds."
}
```
