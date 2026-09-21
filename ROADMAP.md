# 🗺️ System Design PoC Roadmap

This roadmap outlines all core Proof-of-Concepts (PoCs) designed to master distributed system design principles through practical, runnable code.

---

## 📌 Status Legend
- ⏳ **Planned**: In curriculum backlog.
- 🚧 **In Progress**: Currently being designed or implemented.
- ✅ **Completed**: Implemented, tested, and documented.

---

## 🚦 Domain 1: Traffic & Rate Control

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **1.1 Rate Limiting Engines** | Token Bucket, Leaky Bucket, Sliding Window Log, Sliding Window Counter | Cloudflare API Shield, Stripe API Limiter | ✅ Completed | Easy-Medium |
| **1.2 Circuit Breaker & Fallback** | Finite State Machine (Closed $\to$ Open $\to$ Half-Open), Failure Rate Thresholds, Timeout Recovery | Netflix Hystrix, Resilience4j | ✅ Completed | Medium |
| **1.3 Adaptive Load Shedding** | Latency-based congestion control, PID queue draining | Envoy Gateway, AWS Load Balancer | ⏳ Planned | Hard |

---

## 💾 Domain 2: Distributed Data & Storage

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **2.1 Consistent Hashing Ring** | MD5/SHA hash ring, Virtual Nodes (vnodes), Minimal key migration on churn | Amazon DynamoDB, Apache Cassandra, Discord Cache | ✅ Completed | Medium |
| **2.2 Bloom Filter & Scalable Filter** | Bit array, multiple independent hash functions, False positive rate tuning | Google Bigtable, Apache Cassandra (SSTable skip) | ⏳ Planned | Easy-Medium |
| **2.3 LSM-Tree & Write-Ahead Log (WAL)** | Append-only WAL, In-memory MemTable (SkipList/AVL), On-disk SSTable, Leveled/Tiered Compaction | RocksDB, LevelDB, Cassandra Storage Engine | ⏳ Planned | Hard |

---

## ⚖️ Domain 3: Coordination, Consensus & Identity

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **3.1 Distributed 64-bit ID Gen** | Timestamp (41b) + Worker ID (10b) + Sequence (12b) bitwise ID generator | Twitter Snowflake, Instagram Sharding ID | ⏳ Planned | Easy |
| **3.2 Distributed Lock & Fencing** | Redis-style single/multi-instance lock with TTL, heartbeat lease renewal, monotonic fencing tokens | Redlock, ZooKeeper Ephemeral Nodes | ⏳ Planned | Medium-Hard |
| **3.3 Mini Raft Consensus** | Leader election, Heartbeat timeouts, Term transitions, Split-vote mitigation | etcd, HashiCorp Consul, Apache Kafka (KRaft) | ⏳ Planned | Hard |

---

## ⚡ Domain 4: Caching & Data Access Patterns

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **4.1 Thundering Herd & SingleFlight** | Cache Stampede prevention via Mutex/In-flight promise coalescing | Go `sync/singleflight`, Facebook Cache Tier | ⏳ Planned | Medium |
| **4.2 Multi-Tier LRU / LFU Cache** | In-memory L1 cache with fast eviction + Distributed L2 cache + Write-through / Write-back | Redis, Memcached, CDN Edge Cache | ⏳ Planned | Medium |
| **4.3 Read-Repair & Quorum Replicas** | Quorum consistency ($R + W > N$), Read-repair on stale replica detection | Apache Cassandra, Amazon Dynamo | ⏳ Planned | Hard |

---

## 🔄 Domain 5: Reliability & Asynchronous Messaging

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **5.1 Idempotency Key Engine** | Request fingerprinting, In-progress status locking, Response payload caching & atomic replay | Stripe Payment API, PayPal Webhooks | ⏳ Planned | Medium |
| **5.2 Dead Letter Queue & Exponential Backoff** | Exponential backoff with Full Jitter, DLQ routing after max retries | AWS SQS, Apache Kafka Error Handlers | ⏳ Planned | Easy-Medium |
| **5.3 Saga Pattern (Orchestrator)** | Multi-service distributed transaction with compensating forward/rollback actions | Uber Ride Booking, E-Commerce Checkout | ⏳ Planned | Hard |

---

## 📍 Domain 6: Geo-Spatial & Search Indexing

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **6.1 Geohash Proximity Engine** | Base32 Geohashing, 8-neighbor bounding box search, Distance filtering | Uber Driver Dispatch, Yelp Restaurant Finder | ⏳ Planned | Medium |
| **6.2 Inverted Index & BM25 Search** | Text tokenization, stopword filtering, Posting lists with term frequencies & BM25 ranking | Elasticsearch, Apache Lucene | ⏳ Planned | Medium-Hard |

---

## 📈 Suggested Path for Beginners

1. **Step 1**: `01-traffic-control/rate-limiters` $\to$ Master concurrency and time-windowing basics.
2. **Step 2**: `02-distributed-data/consistent-hashing` $\to$ Learn distributed partitioning.
3. **Step 3**: `03-coordination-and-id/snowflake-id-gen` $\to$ Understand coordination-free distributed IDs.
4. **Step 4**: `05-reliability-and-messaging/idempotency-engine` $\to$ Master critical financial/data safety.
5. **Step 5**: `02-distributed-data/lsm-tree-and-wal` $\to$ Deep dive into modern storage engines.
