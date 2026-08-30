# 🚦 PoC 1.1: Rate Limiting Engines

> **Domain:** Traffic Control & Reliability  
> **Status:** ✅ Completed  
> **Concepts:** Token Bucket, Leaky Bucket Meter, Sliding Window Log, Sliding Window Counter

## What you will learn

Rate limiting decides whether a request may use a scarce resource *now*. It is a first line of defence for APIs and services: it limits abusive traffic, protects downstream dependencies during bursts, and enforces fair per-tenant quotas.

This PoC implements four thread-safe, in-memory limiters and runs them through steady traffic, a flash crowd, and recovery. It is deliberately local and educational: a production deployment needs shared state, a client or route key, observability, and a defined failure policy.

## Choose an algorithm in 30 seconds

| If you need to… | Choose | Why | Main trade-off |
| --- | --- | --- | --- |
| Allow a short, legitimate burst while enforcing an average rate | **Token Bucket** | Accumulated tokens can be spent immediately | A large bucket can still spike a downstream service |
| Limit the rate at which work is admitted to a buffer | **Leaky Bucket Meter** | The water level drains at a fixed rate | This PoC admits or rejects work; it does not queue and schedule work itself |
| Enforce an exact rolling quota | **Sliding Window Log** | It records each accepted timestamp | Memory grows with requests in the window |
| Use a rolling quota with predictable, constant memory | **Sliding Window Counter** | It estimates load from two adjacent windows | It is approximate near window boundaries |

## The shared request contract

Every limiter exposes the same operation:

```python
allowed = limiter.allow_request(tokens=1)
```

`True` means the request was admitted; `False` means the caller should reject it, normally with HTTP `429 Too Many Requests`. The implementation also exposes `get_stats()` for the simulator and `reset()` for a fresh state.

All state changes are protected by a lock, so a single process can safely serve concurrent threads. The algorithms use `time.monotonic()` rather than wall-clock time, preventing NTP or system-clock changes from moving a limit backwards or forwards.

## How the four algorithms work

### 1. Token Bucket — burst-friendly average rate

A bucket starts full with capacity `C`. It gains `r` tokens per second, up to `C`. A request costing `k` tokens succeeds only when at least `k` tokens are present.

$$
\text{tokens} = \min(C, \text{tokens} + (\text{now} - \text{last refill}) \times r)
$$

- **Time / space:** $O(1)$ / $O(1)$
- **Good for:** public APIs, tenant plans, and workloads that should tolerate short bursts.
- **Example:** capacity 20 and refill rate 10/s allows an immediate burst of 20, then sustains 10 requests per second.

### 2. Leaky Bucket Meter — drain a bounded backlog

The limiter tracks an abstract `water_level`. Each admitted request adds water; elapsed time drains it at rate `r`. A request is rejected when adding it would exceed capacity `C`.

$$
\text{water level} = \max(0, \text{water level} - (\text{now} - \text{last leak}) \times r)
$$

- **Time / space:** $O(1)$ / $O(1)$
- **Good for:** deciding whether a bounded downstream buffer can absorb more work.
- **Important distinction:** a full traffic shaper stores requests in a FIFO queue and releases them on a schedule. This implementation is a *meter*: it admits or rejects immediately and does not retain request payloads.

### 3. Sliding Window Log — exact rolling limit

For every accepted token, the limiter records a timestamp. Before evaluating a request, it removes timestamps outside the last window. The incoming request is allowed only if the remaining count plus its token cost fits the limit.

- **Time / space:** $O(M)$ eviction / $O(N)$, where $M$ is expired entries removed and $N$ is accepted tokens in the window.
- **Good for:** strict limits such as login attempts or sensitive transaction endpoints.
- **Trade-off:** precision costs memory and timestamp maintenance.

### 4. Sliding Window Counter — low-memory approximation

The limiter keeps a count for the current fixed window and the prior one. It weights the prior count by how much of that window overlaps the current rolling interval.

$$
\text{weight} = \frac{\text{window size} - \text{time into current window}}{\text{window size}}
$$

$$
\text{estimated load} = (\text{previous count} \times \text{weight}) + \text{current count}
$$

- **Time / space:** $O(1)$ / $O(1)$
- **Good for:** high-throughput services where a small approximation error is acceptable.
- **Trade-off:** it assumes requests in the previous window were evenly distributed, which is not always true.

## Compare the trade-offs

| Algorithm | Burst handling | Accuracy | Memory | Best fit |
| --- | --- | --- | --- | --- |
| Token Bucket | Allows bursts up to capacity | Exact for the configured token model | Constant | General API quotas |
| Leaky Bucket Meter | Bounds the modeled backlog | Exact for the configured meter | Constant | Downstream-buffer protection |
| Sliding Window Log | Prevents boundary bursts | Exact rolling count | Proportional to recent traffic | Security and strict quotas |
| Sliding Window Counter | Smooths fixed-window boundaries | Approximate rolling count | Constant | High-volume edge/API limits |

## Run the PoC

Run commands from the repository root using the project virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest 01-traffic-control\rate-limiters\test_rate_limiters.py -v
.venv\Scripts\python.exe 01-traffic-control\rate-limiters\simulate.py
```

The test suite covers basic behavior, refill/expiry behavior, multi-token requests, and concurrent access. The simulator applies three phases to the same limiter instances:

1. **Steady traffic:** 8 requests/sec, below the configured sustained rates.
2. **Flash crowd:** 50 requests/sec, deliberately above the limits.
3. **Recovery:** 8 requests/sec after the burst.

For each phase, compare **Allowed**, **Rejected**, and **Pass Rate**. Token and leaky bucket configurations begin the burst with spare capacity; the sliding-window algorithms enforce their ten-requests-per-second policy more tightly. The final table shows cumulative requests and each limiter's remaining internal state.

## From a local PoC to a distributed limiter

The included Python classes keep state in one process. If several API instances serve a client, each instance would otherwise give that client an independent allowance. A production limiter normally stores state in a shared system such as Redis and executes its read–refill–consume sequence atomically, commonly with a Lua script.

At minimum, a production design must define:

- A stable key, such as `ratelimit:{tenant}:{route}`.
- Atomic state updates across all application instances.
- Key expiration based on the refill horizon.
- Behavior when the shared limiter is unavailable: fail open for availability, or fail closed for protection.
- Metrics, including allowed/rejected counts, latency, and hot keys.

### Redis token bucket sketch

```lua
-- KEYS[1]: e.g. "ratelimit:user:123"
-- ARGV: capacity, refill rate/sec, current timestamp, requested tokens
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local state = redis.call("HMGET", KEYS[1], "tokens", "last_refill")
local tokens = tonumber(state[1]) or capacity
local last_refill = tonumber(state[2]) or now

tokens = math.min(capacity, tokens + math.max(0, now - last_refill) * refill_rate)
if tokens < requested then
    return 0
end

redis.call("HMSET", KEYS[1], "tokens", tokens - requested, "last_refill", now)
redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill_rate))
return 1
```

## HTTP response guidance

When rejecting a request, return `429 Too Many Requests`. Include a `Retry-After` value when you can calculate it, and expose rate-limit headers consistently with your API contract. Do not treat the exact header names below as universal: several standards and vendor conventions coexist.

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 30
Content-Type: application/json

{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Please retry after 30 seconds."
}
```
