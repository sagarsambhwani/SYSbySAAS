---
name: system-design-poc-readme
description: >-
  Creates high-clarity, production-grade README documentation for System Design
  and Distributed Systems Proof-of-Concepts (PoCs). Use when authoring or
  updating documentation for PoC modules, algorithms, simulations, or architecture teardowns.
---

# System Design PoC README Authoring Blueprint

Use this skill to structure and author documentation for System Design Proof-of-Concepts. Every PoC README must serve as both an educational guide and a technical design document.

---

## 🏛️ The 7-Step PoC README Blueprint

### 1. Executive Context Header
- Domain classification (e.g., *Traffic Control*, *Distributed Storage*, *Coordination*).
- Real-world production analogs (e.g., *Stripe*, *Cloudflare*, *AWS*, *Netflix*, *Cassandra*).
- Status indicator and key learning objectives.

### 2. 30-Second Decision Matrix (Top of Document)
Provide an instant decision table before code or deep theory:
| If you need to… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| *[Goal / Requirement]* | **[Algorithm/Pattern]** | *[Core Benefit]* | *[Key Constraint/Drawback]* |

### 3. Unified Interface & Invariants
- Minimal shared public contract (e.g., `allow_request(tokens=1) -> bool`).
- Concurrency & safety invariants (e.g., `threading.Lock`, `time.monotonic()` to prevent NTP time-skew bugs).

### 4. Algorithmic Breakdown with Math & Complexity
For each algorithm/pattern:
- **Core Mechanism & Formulas**: Clean LaTeX equations.
- **Exact Complexity**: Time $O(f(n))$ and Space $O(g(n))$.
- **Nuances & Distinctions**: Explicitly clarify subtle variants (e.g., *Meter* vs *Queue/Traffic Shaper*).

### 5. Multi-Dimensional Trade-off Matrix
Side-by-side comparison across:
- Burst Handling / Traffic Smoothing
- Precision & Accuracy (Exact vs Approximate)
- Memory Footprint under Heavy Load / DDoS
- Best Fit Workloads

### 6. Runnable Lab & Telemetry Interpretation Guide
- Exact commands using the project virtual environment (`.venv`).
- Multi-phase workload walkthrough (**Steady State $\to$ Flash Crowd Burst $\to$ Recovery**).
- Guidance on how to interpret console telemetry (Allowed vs Throttled vs State).

### 7. Bridging to Distributed Architecture
- Explain why local single-process state fails in multi-server architectures.
- Provide a concrete, atomic production pattern (e.g., **Redis Lua script**, Raft state machine, Quorum read-repair).
- Define failure resilience policies (**Fail-Open** vs **Fail-Closed**).
- Detail industry-standard HTTP status codes, headers (`X-RateLimit-*`, `Retry-After`), and error response schemas.

---

## ✅ Quality Checklist
Before finalizing a PoC README, ensure:
- [ ] No abstract hand-waving: formulas and Big-O complexity are explicit.
- [ ] Formulas use clean LaTeX notation (`$...$` and `$$...$$`).
- [ ] Code snippets and commands strictly use the project virtual environment.
- [ ] Distributed production extension is concrete (e.g. includes Lua script or network protocol).
