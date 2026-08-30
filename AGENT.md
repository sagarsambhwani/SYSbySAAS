# 🤖 AGENT.md — Developer & AI Agent Instructions

This document specifies mandatory rules and conventions for all AI agents and human contributors developing within the **SYSbySAAS** repository.

---

## 📌 Critical Directives

### 1. 🔀 Granular Git Commits (Strict Rule)
- **Make commits on a strict file-by-file basis.**
- **NEVER** use `git add .` or commit multiple unrelated or bulk files in a single generic commit.
- Stage and commit individual files (or tightly coupled atomic pairs, such as an implementation file and its direct unit test) one by one.
- Use **Conventional Commit** format with meaningful, descriptive messages:
  - `feat(rate-limiter): add token bucket algorithm implementation`
  - `test(rate-limiter): add concurrency race condition unit tests`
  - `docs(rate-limiter): add architecture tradeoffs and simulation guide`
  - `chore(repo): configure virtual environment and gitignore`

```bash
# Example of correct granular workflow:
git add .gitignore
git commit -m "chore(repo): initialize standard gitignore for python"

git add README.md
git commit -m "docs(repo): add project overview and quick start guide"

git add ROADMAP.md
git commit -m "docs(repo): define system design curriculum roadmap"
```

---

### 2. 🐍 Virtual Environment (Strict Rule)
- **Always use a Python virtual environment (`.venv`).**
- **NEVER** install packages globally into the system Python.
- All script runs, tests, and tool invocations must use the virtual environment interpreter:
  - Windows: `.venv\Scripts\python.exe`
  - Linux/macOS: `.venv/bin/python`

---

## 📐 PoC Structure & Standard Architecture

Every PoC directory must follow this standard 4-part structure:

```
<domain-folder>/<poc-name>/
├── core.py           # Clean, standalone implementation of the algorithm
├── simulate.py       # Multi-threaded / async load simulator with rich visual telemetry
├── test_<poc>.py     # Concurrency, edge-case, and functional tests
└── README.md         # Architecture teardown, CAP/trade-offs, and real-world system comparison
```

### Module Guidelines:
1. **`core.py`**:
   - Focus on readability and minimal dependencies.
   - Include type annotations and docstrings explaining algorithmic complexity (Time $O(n)$, Space $O(n)$).
   - Properly handle thread safety / concurrency (e.g. locks, atomic operations).

2. **`simulate.py`**:
   - Must be runnable directly via `python simulate.py`.
   - Provide visual feedback (e.g., using `rich` console tables, progress bars, or live stats).
   - Simulate realistic scenarios (e.g., sudden burst traffic, node crashes, network partition simulation).

3. **`test_<poc>.py`**:
   - Write deterministic tests with `pytest`.
   - Include concurrency tests that actively verify race condition handling under high thread counts.

4. **`README.md`**:
   - Problem Statement & Why naive approaches fail.
   - Mathematical / Algorithmic breakdown.
   - Real-World Production Usage (e.g., How Stripe, AWS, or Discord solve this).
   - Key Trade-offs (Latency vs Memory vs Consistency).

---

## 🛠️ Testing & Verification Standards

Before completing any task or marking a PoC as complete:
1. Run automated tests inside `.venv`:
   ```bash
   pytest <path-to-poc-test>
   ```
2. Run simulation script to ensure error-free visual execution:
   ```bash
   python <path-to-poc>/simulate.py
   ```
3. Update `ROADMAP.md` status indicator to `✅ Completed`.
4. Perform granular file-by-file git commits.
