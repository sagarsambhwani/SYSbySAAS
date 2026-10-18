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
| **2.2 Bloom Filter & Scalable Filter** | Bit array, multiple independent hash functions, False positive rate tuning | Google Bigtable, Apache Cassandra (SSTable skip) | ✅ Completed | Easy-Medium |
| **2.3 LSM-Tree & Write-Ahead Log (WAL)** | Append-only WAL, In-memory MemTable (SkipList/AVL), On-disk SSTable, Leveled/Tiered Compaction | RocksDB, LevelDB, Cassandra Storage Engine | ⏳ Planned | Hard |

---

## ⚖️ Domain 3: Coordination, Consensus & Identity

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **3.1 Distributed 64-bit ID Gen** | Timestamp (41b) + Worker ID (10b) + Sequence (12b) bitwise ID generator | Twitter Snowflake, Instagram Sharding ID | ✅ Completed | Easy |
| **3.2 Distributed Lock & Fencing** | Redis-style single/multi-instance lock with TTL, heartbeat lease renewal, monotonic fencing tokens | Redlock, ZooKeeper Ephemeral Nodes | ✅ Completed | Medium-Hard |
| **3.3 Mini Raft Consensus** | Leader election, Heartbeat timeouts, Term transitions, Split-vote mitigation | etcd, HashiCorp Consul, Apache Kafka (KRaft) | ⏳ Planned | Hard |

---

## ⚡ Domain 4: Caching & Data Access Patterns

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **4.1 Thundering Herd & SingleFlight** | Cache Stampede prevention via Mutex/In-flight promise coalescing | Go `sync/singleflight`, Facebook Cache Tier | ✅ Completed | Medium |
| **4.2 Multi-Tier LRU / LFU Cache** | In-memory L1 cache with fast eviction + Distributed L2 cache + Write-through / Write-back | Redis, Memcached, CDN Edge Cache | ✅ Completed | Medium |
| **4.3 Read-Repair & Quorum Replicas** | Quorum consistency ($R + W > N$), Read-repair on stale replica detection | Apache Cassandra, Amazon Dynamo | ✅ Completed | Hard |

---

## 🔄 Domain 5: Reliability & Asynchronous Messaging

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **5.1 Idempotency Key Engine** | Request fingerprinting, In-progress status locking, Response payload caching & atomic replay | Stripe Payment API, PayPal Webhooks | ✅ Completed | Medium |
| **5.2 Dead Letter Queue & Exponential Backoff** | Exponential backoff with Full Jitter, DLQ routing after max retries | AWS SQS, Apache Kafka Error Handlers | ✅ Completed | Easy-Medium |
| **5.3 Saga Pattern (Orchestrator)** | Multi-service distributed transaction with compensating forward/rollback actions | Uber Ride Booking, E-Commerce Checkout | ✅ Completed | Hard |

---

## 📍 Domain 6: Geo-Spatial & Search Indexing

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **6.1 Geohash Proximity Engine** | Base32 Geohashing, 8-neighbor bounding box search, Distance filtering | Uber Driver Dispatch, Yelp Restaurant Finder | ✅ Completed | Medium |
| **6.2 Inverted Index & BM25 Search** | Text tokenization, stopword filtering, Posting lists with term frequencies & BM25 ranking | Elasticsearch, Apache Lucene | ✅ Completed | Medium-Hard |

---

## 🏛️ Domain 7: Microservices & Distributed Data Consistency

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **7.1 Transactional Outbox & CDC** | Atomic DB commit + event table insertion, background polling/CDC publisher to message broker | Debezium, Airbnb Outbox, Martin Kleppmann Dual-Write | ⏳ Planned | Medium-Hard |
| **7.2 Distributed Tracing & Context Propagation** | W3C TraceContext (traceparent/tracestate), Span hierarchies, Baggage threading across sync/async boundaries | OpenTelemetry, Jaeger, Zipkin | ⏳ Planned | Medium |
| **7.3 Cascading Timeout & Deadline Trees** | Distributed deadline propagation, remaining budget calculation, premature call cancellation | gRPC Context Deadlines, Envoy Request Timeouts | ⏳ Planned | Medium |

---

## 📜 Domain 8: API Evolution & Contract Governance

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **8.1 Schema Evolution & Wire Compatibility** | Binary tag numbering, backward/forward compatibility rules, field tombstoning and unrecognized field preservation | Protobuf, Apache Avro, Confluent Schema Registry | ⏳ Planned | Medium |
| **8.2 API Versioning & Sunset Engine** | URI vs Header vs Content Negotiation, Sunset/Deprecation HTTP headers, shadow dual-traffic routing | Stripe API Versioning, GitHub REST API, RFC 8594 | ⏳ Planned | Medium |
| **8.3 Consumer-Driven Contract Testing** | Provider verification of consumer contract expectations, breaking change gatekeeper | Pact Framework, Spring Cloud Contract | ⏳ Planned | Medium |

---

## 🛡️ Domain 9: Production Guardrails & Governance ("Red Tape")

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **9.1 Feature Flags & Emergency Kill Switches** | Dynamic rule evaluation, percentage rollout hashing, immediate global kill switches | LaunchDarkly, Flipt, Unleash | ✅ Completed | Easy-Medium |
| **9.2 Cell-Based Architecture Partitioning** | Complete failure-domain isolation, deterministic cell routing, blast radius containment | AWS Cellular Architecture, Slack Cells | ✅ Completed | Hard |
| **9.3 Immutable Audit Trail & Tamper-Evident Ledger** | Cryptographic hash chaining, Merkle tree verification, append-only compliance logging | AWS CloudTrail, Amazon QLDB, Git Commit Chains | ⏳ Planned | Medium-Hard |

---

## 🌊 Domain 10: Event Sourcing & Stream Processing

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **10.1 Event Sourcing & CQRS Projections** | Append-only event store, aggregate rehydration, async read-model projection rebuilding | EventStoreDB, Axon Framework, Banking Ledgers | ⏳ Planned | Hard |
| **10.2 Stream Windowing & Watermarks** | Tumbling, sliding, and session windows with watermark-based late-event handling | Apache Flink, Apache Spark Streaming | ⏳ Planned | Hard |
| **10.3 Partitioned Commit Log Engine** | Segmented disk-backed append logs, consumer group offset tracking, zero-copy read modeling | Apache Kafka, Apache Pulsar, AWS Kinesis | ⏳ Planned | Hard |

---

## 📊 Domain 11: Observability & Telemetry Infrastructure

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **11.1 Real-Time Metrics Aggregator & Percentiles** | Streaming percentile estimation (T-Digest / HdrHistogram), p50/p95/p99 latency tracking | Prometheus, Datadog Agent, StatsD | ⏳ Planned | Medium-Hard |
| **11.2 Distributed Health & Adaptive Probing** | Two-tier readiness vs liveness probes, flapping suppression, cascading restart prevention | Kubernetes Probes, Envoy Active Health Checking | ⏳ Planned | Easy-Medium |
| **11.3 Dynamic Log Sampling & Ingestion** | Adaptive probabilistic sampling, rate-limited burst shedding, structured log enrichment | Vector.dev, Fluent Bit, AWS CloudWatch Logs | ⏳ Planned | Medium |

---

## 🔐 Domain 12: Security, Auth & Access Control

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **12.1 Token Revocation & Distributed Blacklist** | Stateless JWT signature verification + Bloom/Redis revocation bloom list for instant invalidation | Auth0, Okta, OAuth2 Token Revocation (RFC 7009) | ⏳ Planned | Medium |
| **12.2 RBAC & Attribute-Based Policy Engine (ABAC)** | In-memory policy DAG evaluation, hierarchical role resolution, context attributes | AWS IAM Policies, Open Policy Agent (Rego) | ⏳ Planned | Medium-Hard |
| **12.3 Secret Rotation & Key Rollover Manager** | Dual-key verification window, automated zero-downtime key rotation, monotonic versioning | HashiCorp Vault, AWS Secrets Manager | ⏳ Planned | Medium |

---

## 🧠 Domain 13: Modern AI & Vector Infrastructure

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :---: | :---: |
| **13.1 Vector Similarity Search (HNSW Index)** | Hierarchical Navigable Small World graphs, Cosine / Euclidean distance sub-millisecond retrieval | Milvus, Pinecone, Qdrant, FAISS | ⏳ Planned | Hard |
| **13.2 Semantic Caching & LLM Token Limiter** | Embedding proximity response reuse, per-user TPM/RPM sliding window token rate limiter | GPTCache, Cloudflare AI Gateway, Redis Semantic Cache | ⏳ Planned | Medium-Hard |
| **13.3 RAG Ingestion & Chunking Pipeline** | Recursive character text splitting, document embedding generation, metadata filtering index | LangChain Ingestion, LlamaIndex VectorStoreIndex | ⏳ Planned | Medium |

---

## 🌐 Domain 14: Real-Time Networking & Edge Systems

| PoC | Core Problem / Algorithm | Real-World Analog | Status | Difficulty |
| :--- | :--- | :--- | :--- | :---: |
| **14.1 Persistent WebSocket Connection Manager** | Connection pooling, heartbeat ping/pong keepalives, dead connection reaping, channel broadcast | Discord Gateway, Slack Real-Time Messaging | ⏳ Planned | Medium |
| **14.2 Reverse Proxy & Layer 7 Traffic Splitter** | Path routing, weighted canary traffic splitting, Least-Connections load balancing algorithm | Nginx, Traefik, HAProxy, Envoy | ⏳ Planned | Medium |

---

## 📈 Suggested Learning Pathways

### Path A: The Distributed Systems Core (Algorithms & Storage)
1. `01-traffic-control/rate-limiters` $\to$ Concurrency & time-windowing basics
2. `02-distributed-data/consistent-hashing` $\to$ Distributed partitioning
3. `03-coordination-and-id/snowflake-id-gen` $\to$ Coordination-free distributed IDs
4. `04-caching-patterns/thundering-herd` $\to$ Concurrency coalescing
5. `02-distributed-data/lsm-tree-and-wal` $\to$ Storage engines & write-ahead logs
6. `03-coordination-and-id/mini-raft` $\to$ Distributed consensus

### Path B: The Real-World Microservices & Enterprise Stack
1. `05-reliability-and-messaging/idempotency-engine` $\to$ Financial & API safety
2. `05-reliability-and-messaging/saga-orchestrator` $\to$ Distributed transactions
3. `07-microservices/transactional-outbox` $\to$ The dual-write solution
4. `08-api-evolution/schema-evolution` $\to$ Safe protocol changes
5. `09-production-guardrails/feature-flags` $\to$ Decoupling deploy from release
6. `07-microservices/deadline-propagation` $\to$ Cascading timeout prevention

### Path C: Modern Data & AI Infrastructure
1. `06-geospatial-and-search/inverted-index-bm25` $\to$ Lexical search fundamentals
2. `10-event-streaming/partitioned-commit-log` $\to$ Kafka-style sequential logs
3. `13-ai-and-vector/vector-similarity-hnsw` $\to$ Approximate nearest neighbor search
4. `13-ai-and-vector/semantic-caching` $\to$ AI cost & latency optimization
