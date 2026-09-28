# 🔒 PoC 3.2: Distributed Lock with Lease Renewal & Fencing Tokens

> **Domain:** Coordination, Consensus & Identity  
> **Status:** ✅ Completed  
> **Real-World Analogs:** Redis (Redlock), Apache ZooKeeper (Ephemeral Nodes), HashiCorp Consul, etcd, AWS DynamoDB Conditional Writes

---

## 🎯 What You Will Learn

In distributed systems, engineers frequently use Redis locks (`SET key val NX PX 5000`) to synchronize access across microservices. However, **relying purely on lock expiration timers (TTLs) is fundamentally flawed** and can cause silent data corruption.

If a client experiences a Stop-the-World Garbage Collection (GC) pause, an OS page fault, or network delay, its lock TTL will expire while it is asleep. A second client acquires the lock and writes. When the first client wakes up, it believes it still owns the lock and overwrites the second client's data.

This PoC implements a complete distributed lock architecture with:
1. **Mutual Exclusion & Deadlock Freedom**: Atomic lock acquisition with automatic TTL expiration.
2. **Safe Atomic Release**: Preventing clients from releasing locks that were re-assigned after expiration.
3. **Background Watchdog (Heartbeat Lease Renewal)**: Extending the lease automatically for active healthy jobs so long tasks never prematurely lose their lock.
4. **Martin Kleppmann's Monotonic Fencing Tokens**: Enforcing storage-layer validation that mathematically eliminates split-brain corruption during process freezes.

---

## ⏱️ 30-Second Decision Matrix

| If your requirement is… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| Coordinate execution between microservices with storage validation | **Distributed Lock + Fencing Tokens** | Eliminates split-brain data corruption even across GC pauses | Storage backend must implement version/token checks |
| Single relational database with row-level updates | **Optimistic Concurrency Control (OCC)** | Uses `WHERE version = current_version`; zero lock overhead | High retry rate under heavy write contention |
| Simple coarse-grained coordination where rare duplicates are harmless | **Basic Redis TTL Lock** | Extremely fast in-memory acquisition | Vulnerable to GC pause split-brain overwrites |
| Distributed consensus for configuration & leader election | **etcd / ZooKeeper Leases** | Strong CP consensus with heartbeat keep-alives | Slower write throughput than in-memory Redis |

---

## 🔌 The Public Request Contract

The lock manager and fenced storage expose a clean, thread-safe public API:

```python
from core import DistributedLockManager, FencedStorageResource

manager = DistributedLockManager()
storage = FencedStorageResource(initial_value="INIT")

# 1. Acquire lock with 3s TTL and automatic Watchdog renewal
handle = manager.acquire(
    resource="order:10492",
    owner="worker-pod-1",
    ttl_seconds=3.0,
    enable_watchdog=True,  # Keeps lease alive while worker is healthy
)

# 2. Extract monotonic fencing token from lock handle
fencing_token = handle.fencing_token  # e.g. #42

# 3. Perform storage write protected by fencing token
storage.write_with_fencing(
    value="ORDER_CONFIRMED",
    client_id="worker-pod-1",
    fencing_token=fencing_token,
)

# 4. Safely release lock (stops watchdog)
handle.release()
```

### Safety & Concurrency Invariants
- **Atomic Acquisition**: Only one client can acquire a resource until it is released or expired.
- **Safe Release**: A client can *only* release a lock if it still matches its unique owner ID and fencing token.
- **Storage-Layer Fencing**: The storage resource rejects any write where $\text{token} \le \text{highest\_seen\_token}$.

---

## 🔬 The GC Pause Problem & Fencing Token Defense

```
WITHOUT FENCING (Silent Corruption):
Client 1: [Acquire Lock] ──(Working)──► [ 1.2s Full GC Pause... ] ──────────────► [Write: $1000] (Corrupts Data!)
                                                │
                                          (TTL Expires!)
                                                │
Client 2:                                 [Acquire Lock] ──► [Write: $0]

WITH FENCING TOKENS (Protected!):
Client 1: [Acquire (Token #1)] ───────► [ 1.2s Full GC Pause... ] ──────────────► [Write (Token #1)]
                                                │                                       │
                                          (TTL Expires!)                                ▼
                                                │                              [Storage Rejects!]
Client 2:                                 [Acquire (Token #2)] ──► [Storage Accepts Token #2]
                                                                   (Highest Token is now #2)
```

### Why Naive TTLs Fail
1. In an asynchronous network, you cannot make assumptions about execution time.
2. A thread can be paused at *any line of code* by:
   - Language garbage collection (JVM, Go, Python).
   - Virtual machine hypervisor CPU descheduling.
   - Operating system page faults or swapping.
3. Therefore, **a lock service cannot guarantee mutual exclusion at the storage layer on its own**. Safety requires cooperation between the lock manager (issuing monotonic tokens) and the storage layer (validating tokens).

---

## 📊 Multi-Dimensional Comparison Matrix

| Property | Basic Redis Lock | Redis Lock + Watchdog | Lock + Fencing Tokens | Optimistic Locking (OCC) |
| :--- | :---: | :---: | :---: | :---: |
| **Prevents Long-Job Expiration?** | ❌ No | ✅ Yes (Auto-renews) | ✅ Yes | N/A |
| **Prevents GC Pause Split-Brain?** | ❌ No (Vulnerable) | ❌ No (Watchdog pauses too) | ✅ **Yes (Guaranteed)** | ✅ Yes |
| **Storage Layer Requirement** | None | None | Needs version/token column | Needs version column |
| **Lock Cleanup on Crash** | Automatic (TTL) | Automatic (TTL) | Automatic (TTL) | Immediate |
| **Write Contention Handling** | Queues / Retries | Queues / Retries | Stale writes rejected | Aborts & retries |

---

## 🧪 Runnable Lab & Simulation Guide

Ensure your virtual environment is active:

```powershell
# Run unit, lease expiration, and concurrency tests
.venv\Scripts\python.exe -m pytest 03-coordination-and-id\distributed-lock\test_distributed_lock.py -v

# Run the live GC pause and fencing token simulation
.venv\Scripts\python.exe 03-coordination-and-id\distributed-lock\simulate.py
```

### Empirical Simulation Results:
1. **Scenario 1: The GC Pause Catastrophe (Without Fencing)**:
   - Client 1 acquires 0.8s lock $\to$ freezes for 1.2s.
   - Client 2 acquires lock and writes `ORDER_CANCELLED`.
   - Client 1 wakes up and blindly writes `ORDER_SHIPPED` $\implies$ **Silent Data Corruption Confirmed!**
2. **Scenario 2: The Fencing Token Shield (With Fencing)**:
   - Client 1 receives Token `#1` $\to$ freezes for 1.2s.
   - Client 2 receives Token `#2` $\to$ writes `ORDER_CANCELLED` (storage advances to `#2`).
   - Client 1 wakes up with Token `#1` $\implies$ **Blocked by storage layer (`StaleFencingTokenException`)! Data Integrity 100% Preserved!**
3. **Scenario 3: Watchdog in Action**:
   - Long job runs for 1.2s on an initial 0.5s TTL.
   - Watchdog extends lease every 0.33s; intruders are consistently blocked throughout.

---

## 🌐 Bridging to Distributed Production Architecture

### 1. Atomic Redis Lua Script for Safe Release
In production Redis, releasing a lock must verify ownership atomically to prevent deleting another client's lock:

```lua
-- KEYS[1]: resource key (e.g. "lock:order:10492")
-- ARGV[1]: expected owner token
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0 -- Lock was lost or reassigned!
end
```

### 2. Implementing Fencing in SQL Databases
In PostgreSQL or MySQL, your tables should include a `fencing_token` column:

```sql
UPDATE orders 
SET status = 'SHIPPED', fencing_token = 42 
WHERE order_id = 10492 AND fencing_token < 42;
```
If another transaction already wrote with token 42 or higher, the `UPDATE` modifies 0 rows, and your application knows the write was stale.

### 3. Implementing Fencing in AWS DynamoDB
In DynamoDB, conditional writes enforce fencing natively:

```python
table.update_item(
    Key={'order_id': '10492'},
    UpdateExpression="SET #s = :status, fencing_token = :token",
    ConditionExpression="attribute_not_exists(fencing_token) OR fencing_token < :token",
    ExpressionAttributeNames={'#s': 'status'},
    ExpressionAttributeValues={':status': 'SHIPPED', ':token': 42}
)
```
If a stale worker attempts a write, DynamoDB raises `ConditionalCheckFailedException`.
