# 🌸 PoC 2.2: Bloom Filter & Scalable Filter

> **Domain:** Distributed Data & Storage  
> **Status:** ✅ Completed  
> **Real-World Analogs:** Google Bigtable, Apache Cassandra (SSTable skip-read), Google Chrome Safe Browsing, Medium Recommendation Feeds, Web-Crawler URL Deduplication

---

## 🎯 What You Will Learn

In high-throughput databases and search engines, querying for non-existent keys is an expensive, silent performance killer. A query for a missing key forces the database to scan in-memory caches, open index files, and perform physical disk seeks across multiple files before finally returning `404 Not Found`.

This PoC implements a space-optimized **Standard Bloom Filter**, a **Counting Bloom Filter** (supporting deletions), and a **Scalable Bloom Filter** (unbounded dynamic growth). It demonstrates:

1. **The Zero False-Negative Invariant**: If the filter says "NO", the item is **100% guaranteed not to exist**—completely eliminating disk I/O.
2. **Extreme Memory Compression**: Storing membership for 1,000,000 items in **1.14 MB of RAM** ($\approx 9.6\text{ bits/item}$) instead of 136 MB in a standard hash table (**99.2% memory savings**).
3. **Kirsch-Mitzenmacher Double Hashing**: Generating $k$ independent hash positions using only two 64-bit hash values ($g_i(x) = (h_1 + i \cdot h_2) \pmod m$) without computing $k$ separate cryptographic hashes.
4. **SSTable Disk Shielding**: Intercepting malicious and non-existent query storms in RAM before they exhaust disk IOPS.

---

## ⏱️ 30-Second Decision Matrix

| If your requirement is… | Choose | Why | Main Trade-off |
| :--- | :--- | :--- | :--- |
| Ultra-compact membership check where items are never deleted | **Standard Bloom Filter** | Maximum bit compression (~9.6 bits/item for 1% error); $O(k)$ lookup | Cannot delete items; capacity is fixed at creation |
| Need to delete items dynamically | **Counting Bloom Filter** | Replaces bits with 4-bit or 8-bit counters; supports `remove()` | Consumes $4\times$ to $8\times$ more memory than Standard Bloom |
| Unbounded streaming data where total count is unknown | **Scalable Bloom Filter** | Auto-adds layered sub-filters with tightening error rates ($p \cdot r^i$) | Slightly slower lookup ($O(L \cdot k)$ across $L$ layers) |
| Require deletions AND maximum bit density | **Cuckoo Filter** | Uses cuckoo hashing on fingerprints; supports deletions | Complex bucket eviction; degrades under high load factors (>95%) |

---

## 🔌 The Public Request Contract

All filter variants expose an identical, Pythonic membership contract:

```python
from core import StandardBloomFilter, CountingBloomFilter, ScalableBloomFilter

# 1. Standard Bloom Filter (1M items, 1% false positive rate)
bf = StandardBloomFilter(capacity=1_000_000, error_rate=0.01)
bf.add("user_session:10492")

# Fast O(1) membership check
if "user_session:10492" in bf:  # or bf.contains(...)
    # Item MIGHT exist (99% probability) -> Proceed to fetch from Disk/DB
    record = database.read_from_disk("user_session:10492")
else:
    # Item DEFINITELY DOES NOT EXIST (100% Guaranteed) -> Return 404 immediately!
    return None

# 2. Counting Bloom Filter (supports removal)
cbf = CountingBloomFilter(capacity=10_000, error_rate=0.01)
cbf.add("transient_token")
cbf.remove("transient_token")  # Decrements counters

# 3. Scalable Bloom Filter (unbounded stream)
sbf = ScalableBloomFilter(initial_capacity=1000, error_rate=0.01)
# Seamlessly absorbs 10M stream items by auto-layering sub-filters
```

### Safety & Concurrency Invariants
- **Thread Safety**: All state changes (`add`, `remove`) and checks (`contains`) are synchronized using `threading.RLock()`.
- **Zero False Negatives**: A Bloom filter can never return `False` if an item was previously added. If `contains(key)` returns `False`, disk read is 100% unnecessary.

---

## 🔬 Mathematical Deep Dive

```
Bit Array (m bits):
Index:       0   1   2   3   4   5   6   7   8   9  ...  m-1
Bits:      [ 0 | 1 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | ... | 0 ]
                 ▲   ▲           ▲           ▲
                 │   │           │           │
           h1(x)─┘   │           │           └──h3(x)
                     h2(x)───────┘
```

### 1. Optimal Bit Array Size ($m$)
Given capacity $n$ and target false positive probability $p$:
$$m = \left\lceil -\frac{n \ln p}{(\ln 2)^2} \right\rceil \approx -1.4427 \cdot n \ln p$$
*For $p = 0.01$ (1% error rate), $m \approx 9.56 \times n$ bits (less than 1.2 bytes per item).*

### 2. Optimal Number of Hash Functions ($k$)
$$k = \left\lceil \frac{m}{n} \ln 2 \right\rceil = \left\lceil -\log_2 p \right\rceil$$
*For $p = 0.01$, optimal $k = 7$ hash functions.*

### 3. Kirsch-Mitzenmacher Double Hashing Optimization
Calculating 7 independent cryptographic hashes (MD5, SHA-256) per item is CPU-prohibitive. We implement the Kirsch-Mitzenmacher theorem:
$$g_i(x) = \Big(h_1(x) + i \cdot h_2(x)\Big) \pmod m \quad \text{for } i \in [0, k-1]$$
Using two 64-bit integer halves from a single MD5 digest produces asymptotic false positive rates identical to $k$ independent hash functions in $O(1)$ CPU time.

---

## 📊 Multi-Dimensional Comparison Matrix

| Data Structure | Membership Time | Memory for 1M Keys | Supports Delete? | False Negatives | False Positives |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Python `set()` (Hash Set)** | $O(1)$ | $\approx 136.4\text{ MB}$ | ✅ Yes | 0% | 0% |
| **Standard Bloom Filter** | $O(k)$ | **$1.14\text{ MB}$** | ❌ No | **0% (Guaranteed)** | $\le 1.0\%$ (Configurable) |
| **Counting Bloom Filter** | $O(k)$ | $\approx 9.15\text{ MB}$ | ✅ Yes | **0% (Guaranteed)** | $\le 1.0\%$ |
| **Scalable Bloom Filter** | $O(L \cdot k)$ | Grows dynamically | ❌ No | **0% (Guaranteed)** | $\le 1.0\%$ (Cumulative) |

---

## 🧪 Runnable Lab & Simulation Guide

Ensure your virtual environment is active:

```powershell
# Run unit, statistical accuracy, and concurrency tests
.venv\Scripts\python.exe -m pytest 02-distributed-data\bloom-filter\test_bloom_filter.py -v

# Run the live benchmark (Disk seek shield + RAM footprint + Saturation curve)
.venv\Scripts\python.exe 02-distributed-data\bloom-filter\simulate.py
```

### Empirical Simulation Results:
1. **Disk I/O Shield Test**:
   - 10,000 queries (5,000 valid + 5,000 non-existent ghost queries).
   - **Without Bloom Filter**: 10,000 disk reads (137.7s execution time).
   - **With Bloom Filter**: 4,951 disk seeks intercepted in RAM in 0ms (71.7s execution time $\to$ **1.9x speedup, 49.5% disk I/O eliminated!**).
2. **Memory Footprint Showdown**:
   - 1,000,000 items in Python `set()`: **136.4 MB** (~143 bytes/item).
   - 1,000,000 items in `StandardBloomFilter`: **1.14 MB** (~1.20 bytes/item $\to$ **99.2% less RAM**).
3. **Saturation Curve**:
   - At 100% capacity: Empirical error rate = **1.05%** (matches theoretical 1.00%).
   - At 250% capacity: Empirical error rate gracefully rises to **29.40%**.

---

## 🌐 Bridging to Distributed Production Architecture

### 1. Apache Cassandra & Google Bigtable (SSTable Skip-Read)
- In LSM-tree storage engines, data is flushed to immutable disk files called **SSTables**.
- A single row key might exist in 1 out of 20 SSTable files on disk.
- Without a Bloom filter: The DB must perform 20 disk seeks per query.
- With a Bloom filter: Every SSTable has an in-memory Bloom filter. The DB tests the filter first, skips 19 files in 0ms, and only reads the 1 file that actually contains the data.

### 2. RedisBloom Module (Centralized Probabilistic Cache)
In distributed microservices, you can run Bloom filters at the network edge using Redis with the **RedisBloom** extension:
```bash
# Add username to distributed filter
BF.ADD usernames_bloom "alice_99"

# Test membership before running expensive SQL query
BF.EXISTS usernames_bloom "ghost_user" # Returns 0 -> Immediately return 404!
```

### 3. LLM Pretraining Data Deduplication
- Training datasets (Common Crawl, RedPajama) contain tens of billions of web pages.
- Duplicate documents degrade model performance and waste thousands of GPU hours.
- Web scrapers compute 64-bit SimHash/MinHash fingerprints of every page and check a multi-gigabyte Scalable Bloom Filter to discard duplicate pages on the fly without querying a central database.
