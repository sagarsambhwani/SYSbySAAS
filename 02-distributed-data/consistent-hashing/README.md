# ⭕ PoC 2.1: Consistent Hashing Ring with Virtual Nodes

> **Domain:** Distributed Data & Storage  
> **Status:** 🚧 In Progress  
> **Real-World Analogs:** Amazon DynamoDB, Apache Cassandra, Discord Voice Routing, GitHub Spokes (Git Storage), Akamai CDN

---

## 🎯 What You Will Learn

In distributed databases and caching tiers, assigning data to servers using a naive modulo operation (`hash(key) % N`) causes a **catastrophic cache stampede**: when a single node joins or crashes, **almost 75% to 100% of all keys are remapped to different servers**, knocking out downstream databases under a thundering herd.

This PoC implements a production-grade **Consistent Hash Ring** with **Virtual Nodes (`vnodes`)** and **DynamoDB-style Replication Preference Lists**. It demonstrates:

1. **Minimal Key Churn**: When a node dies or joins, only $\approx \frac{1}{N}$ of keys are migrated. The remaining keys stay warm and untouched.
2. **Virtual Node (`vnode`) Dispersion**: Eliminating non-uniform data skew (hotspots) by sprinkling multiple virtual positions across the ring per physical machine.
3. **Replication & Quorum Preference Lists**: Finding the next $R$ *distinct physical nodes* along the ring for fault-tolerant read/write quorums ($R + W > N$).
4. **CDC & Shard Routing**: How modern Change Data Capture engines (Debezium, Kafka) use consistent hashing to guarantee strict per-entity ordering without serialization bottlenecks.

---

## ⏱️ 30-Second Decision Matrix

| If your requirement is… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| Distribute keys across dynamic nodes with minimal rebalancing | **Consistent Hashing with vnodes** | Only $1/N$ keys move on node churn; vnodes balance load uniformly | Lookup is $O(\log(N \cdot V))$ via binary search instead of $O(1)$ |
| Fixed, immutable set of static servers that never changes | **Modulo Hashing (`hash % N`)** | Simple $O(1)$ array index lookup | Total cluster invalidation ($>75\%$ churn) if any server dies |
| Zero coordinate ring maintenance for high-concurrency proxies | **Rendezvous (HRW) Hashing** | Highest-weight hash wins; zero ring structure in memory | $O(N)$ hash evaluations per lookup across all nodes |
| Dynamic sharding where rows must be split by custom range | **Range-Based Partitioning** | Supports efficient range queries (`WHERE age BETWEEN 20 AND 30`) | Suffers write hotspots on monotonically increasing keys (e.g. timestamps) |

---

## 🔌 The Public Request Contract

The `ConsistentHashRing` exposes a clean, thread-safe public API:

```python
# 1. Initialize the ring with physical nodes (150 vnodes each by default)
ring = ConsistentHashRing(nodes=["node-A", "node-B", "node-C"], vnodes=150)

# 2. Look up the primary coordinator node owning a key
primary_node = ring.get_node("user_session:10492")  # -> "node-B"

# 3. Get DynamoDB-style replication set (3 distinct physical nodes)
replicas = ring.get_preference_list("user_session:10492", replication_factor=3)
# -> ["node-B", "node-C", "node-A"]

# 4. Dynamically scale cluster up or down with minimal migration
ring.add_node("node-D", weight=1.5)  # 1.5x capacity -> 225 vnodes
ring.remove_node("node-B")           # Graceful drain / crash recovery
```

### Safety & Concurrency Invariants
- **Thread Safety**: Ring mutations (`add_node`, `remove_node`) and lookups (`get_node`, `get_preference_list`) are synchronized using `threading.RLock()`.
- **Deterministic Hashing**: Keys and node tokens are mapped using 32-bit MD5 / Murmur3 digests to guarantee identical key placement across all cluster instances without hash-seed drift.

---

## 🔬 How the Circular Hash Ring Works

```
                              0 / 2^32 - 1
                                    │
                       [Node-A#12]  │  [Node-C#4]
                          \         │    /
                           \        │   /
                            \       │  /
     270° ──────────────────────────┼────────────────────────── 90°
                            /       │   \
                           /        │    \  Key: "user_99"
               [Node-B#88]          │     \ (walks clockwise) ──► Node-C#4
                                    │
                               [Node-A#54]
                                    │
                                   180°
```

### 1. The Ring Topology
The hash space is treated as a continuous circle spanning $[0, 2^{32}-1]$ ($0$ to $\approx 4.29 \times 10^9$). When a token reaches $2^{32}-1$, it wraps around to $0$.

### 2. Placing Servers (Virtual Nodes)
To avoid uneven arcs, every physical server $S_i$ is mapped to $V$ virtual token points across the ring:
$$\text{Token}(S_i, k) = \text{hash}\Big(S_i + \text{"#vnode\_"} + k\Big) \quad \text{for } k \in [0, V-1]$$
These tokens are sorted in an array: $T = [t_0, t_1, t_2, \dots, t_{M-1}]$ where $M = N \times V$.

### 3. Placing Keys and the Clockwise Walk
When a key $K$ arrives:
1. Calculate its position: $h_K = \text{hash}(K)$.
2. Use **Binary Search (`bisect_right`)** to find the smallest token $t_i \ge h_K$.
3. If $h_K > t_{M-1}$ (past the last token on the ring), wrap around to $t_0$.
4. The node associated with that token is the primary owner!

$$\text{Lookup Complexity:} \quad O(\log(N \times V))$$

---

## ⚖️ Why Virtual Nodes (`vnodes`) Are Mandatory

Without virtual nodes ($V = 1$), placing 3 servers randomly on a circle of 4.2 billion points results in massive **data skew**:

```
WITHOUT VNODES (V=1):
[Node-A] (owns 82% of ring) ────────────► [Node-B] (12%) ──► [Node-C] (6%)
⚠️ Node-A suffers severe memory exhaustion and crashes under heavy load!

WITH VNODES (V=150):
[A]─[C]─[B]─[A]─[B]─[C]─[A]─[B]─[C]─[A]─[C]─[B]─[A]─[B]─[C]
✅ Virtual nodes are evenly interleaved. Every physical server owns ~33.3% of the keys.
```

### Advantages of $V = 150$:
1. **Hotspot Elimination**: Reduces standard deviation of key counts across nodes to $< 5\%$.
2. **Even Cascading Failover**: When physical `Node-B` dies, its 150 virtual nodes disappear from 150 different spots on the ring. Its load is split **$50/50$ evenly across Node-A and Node-C**, rather than crushing a single neighbor!
3. **Heterogeneous Hardware**: A server with $2\times$ RAM/CPU can be given `weight=2.0` ($300$ vnodes), naturally drawing $2\times$ the traffic without custom routing logic.

---

## 📊 Multi-Dimensional Comparison Matrix

| Sharding Strategy | Churn on Crash ($N \to N-1$) | Churn on Scale ($N \to N+1$) | Lookup Complexity | Uniform Distribution | Memory Overhead |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Modulo Hashing (`hash % N`)** | $\approx \frac{N-1}{N}$ (**75% - 95%**) | $\approx \frac{N}{N+1}$ (**80% - 98%**) | $O(1)$ | High | None ($O(1)$) |
| **Consistent Hashing ($V=1$)** | $\approx \frac{1}{N}$ (**20% - 25%**) | $\approx \frac{1}{N+1}$ (**15% - 20%**) | $O(\log N)$ | ❌ Poor (Skewed) | Minimal ($N$ tokens) |
| **Consistent Hashing ($V=150$)** | $\approx \frac{1}{N}$ (**20% - 25%**) | $\approx \frac{1}{N+1}$ (**15% - 20%**) | $O(\log(N \cdot V))$ | ✅ Excellent ($<5\%$ std dev) | Moderate ($N \cdot 150$ tokens) |
| **Rendezvous Hashing (HRW)** | $\approx \frac{1}{N}$ (**20% - 25%**) | $\approx \frac{1}{N+1}$ (**15% - 20%**) | $O(N)$ | ✅ Excellent | None ($O(1)$) |

---

## 🧪 Runnable Lab & Simulation Guide

Once the implementation files are generated, execute using the project virtual environment:

```powershell
# Run unit, churn invariance, and concurrency tests
.venv\Scripts\python.exe -m pytest 02-distributed-data\consistent-hashing\test_consistent_hashing.py -v

# Run the live interactive terminal simulation
.venv\Scripts\python.exe 02-distributed-data\consistent-hashing\simulate.py
```

### What the Simulation Benchmarks:
1. **The Crash Test (Modulo vs Ring Churn)**:
   - Distributes 10,000 keys across 4 nodes.
   - Node 4 crashes $\to$ Measures how many keys changed server ownership.
   - **Modulo Hashing**: Scrambles $\approx 7,500$ keys ($75\%$).
   - **Consistent Hashing**: Moves only $\approx 2,500$ keys ($25\%$), leaving $7,500$ keys warm in cache!
2. **The Hotspot Standard Deviation Test**:
   - Visualizes key allocation across nodes with $V=1$ vs $V=150$, proving mathematical variance reduction.

---

## 🌐 Bridging to Distributed Production Architecture

### 1. DynamoDB / Cassandra Replication (Preference Lists)
In masterless distributed storage (Amazon DynamoDB paper, Apache Cassandra):
- A write must be replicated across $R$ nodes (e.g. $R = 3$).
- The client hashes `partition_key` onto the ring to find the coordinator node.
- The coordinator walks clockwise to collect the **next $R$ unique physical nodes** (skipping duplicate vnodes of the same server).
- Writes execute using Quorum: $W + R > N$ (e.g., Write to 2 nodes, Read from 2 nodes) ensuring strong consistency.

### 2. GitHub Spokes (DGit Storage Sharding)
- GitHub stores hundreds of millions of Git repositories.
- Each repository ID is placed on a consistent hash ring to determine which file server cluster holds the bare Git repository.
- When disk space runs low, new storage nodes are attached to the ring; only a small slice of repositories stream over the network to the new server via background migration without taking down GitHub.

### 3. CDC Stream Partitioning (Debezium & Kafka)
- In Change Data Capture pipelines, ordering of row mutations (`INSERT`, `UPDATE`, `DELETE`) is paramount.
- Consistent hashing on the primary key ensures that all mutations for `account_id=982` always route to the exact same Kafka partition and consumer worker, avoiding race-condition data corruption while scaling out parallel processing.
