# SYSbySAAS — Real-World System Design Proof-of-Concepts (PoCs)

A hands-on, runnable laboratory of isolated distributed systems patterns and mechanisms. Rather than reading abstract high-level diagrams, **SYSbySAAS** provides clean, standalone implementations of core distributed algorithms paired with multi-threaded/async stress simulators and real-time terminal telemetry.

---

## 🎯 Philosophy

1. **One Concept, One PoC**: Each module isolates a single distributed mechanism (e.g., Token Bucket rate limiting, Consistent Hashing ring, Idempotency keys) without the noise of monolithic frameworks.
2. **Deterministic Simulations**: Every PoC comes with a load generator or race condition reproducer to observe the system under stress.
3. **Zero-Magic & Educational**: Idiomatic, well-commented code prioritizing clarity, edge-case handling, and algorithmic trade-offs (CAP theorem, latency vs throughput).

---

## 📂 Repository Structure

```
SYSbySAAS/
├── 01-traffic-control/
│   ├── rate-limiters/            # Token Bucket, Leaky Bucket, Sliding Window Log/Counter
│   └── circuit-breaker/          # State Machine (Closed/Open/Half-Open) with Fallbacks
│
├── 02-distributed-data/
│   ├── consistent-hashing/       # Ring topology, virtual nodes, minimal rebalancing
│   ├── bloom-filter/             # Probabilistic set membership before disk/DB access
│   └── lsm-tree-and-wal/         # Write-Ahead Log + MemTable + SSTable compaction
│
├── 03-coordination-and-id/
│   ├── snowflake-id-gen/         # 64-bit time-ordered distributed ID generation
│   ├── distributed-lock/         # Lock renewal, fencing tokens, and TTL leases
│   └── leader-election-raft/     # Consensus heartbeat and election protocol
│
├── 04-caching-patterns/
│   ├── thundering-herd/          # Mutex / SingleFlight cache stampede protection
│   └── multi-tier-lru-lfu/       # Multi-level caching with invalidation strategies
│
├── 05-reliability-and-messaging/
│   ├── idempotency-engine/       # Exactly-once request processing with replay guards
│   ├── dead-letter-queue-retry/  # Exponential backoff, jitter, and DLQ handling
│   └── saga-orchestrator/        # Distributed transactions & compensating workflows
│
└── 06-location-and-search/
    ├── geohash-proximity/        # Spatial indexing for proximity & neighbor lookups
    └── inverted-index/           # Document tokenization, postings lists & BM25 scoring
```

---

## ⚡ Quick Start

### 1. Environment Setup
Always use a Python virtual environment:

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install common dependencies (rich, pytest, etc.)
pip install -r requirements.txt
```

### 2. Running a PoC Simulation
Navigate to any PoC directory and run its interactive simulation:

```bash
# Example: Run Consistent Hashing visualizer
python 02-distributed-data/consistent-hashing/simulate.py
```

---

## 🗺️ Roadmap & Tracking

Check out [`ROADMAP.md`](./ROADMAP.md) for the complete list of planned and completed PoCs, algorithmic comparisons, and real-world system case studies.

---

## 🤖 Agent & Contributor Guidelines

Please review [`AGENT.md`](./AGENT.md) before submitting code. Key rules:
- **Granular Git Commits**: Commit changes on a strict file-by-file basis using conventional commit messages (`feat:`, `docs:`, `fix:`).
- **Mandatory Virtual Environment**: Always execute and test within `.venv`.
