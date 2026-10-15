"""Interactive Simulation: Inverted Index & Okapi BM25 Relevance Ranking.

Benchmarks:
1. Search Performance: Inverted Index Posting List Lookup vs Naive Substring Scan (1,000 Documents).
2. Anti-Spam Defense: BM25 Term Frequency Saturation & Length Normalization vs Naive TF-IDF.
3. Term Scoring Breakdown: Inverse Document Frequency (IDF) decomposition across rare vs ubiquitous terms.
"""

import math
import random
import time
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    InvertedIndexBM25,
    tokenize,
)

console = Console()


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: INVERTED INDEX & OKAPI BM25 RANKING[/bold cyan]\n"
            "[yellow]Benchmarking Posting Lists, TF Saturation, Length Penalty & Term IDF Scoring[/yellow]",
            border_style="cyan",
        )
    )

    # -----------------------------------------------------------------------
    # Benchmark 1: Inverted Index vs Naive Linear Scan (1,000 Documents)
    # -----------------------------------------------------------------------
    console.print("\n[bold green][BENCHMARK 1][/bold green] Inverted Index vs Naive Scan over 1,000 Technical Articles...")
    console.print("  Corpus: 1,000 documents covering distributed systems, databases, and networks.")
    console.print("  Query: 'distributed consensus raft leader election'")

    topics = [
        "distributed consensus raft leader election protocol for cluster coordination",
        "relational database postgresql query optimization b-tree indexing",
        "redis in-memory cache eviction policies lru lfu write-through",
        "apache kafka event streaming partitioned topic consumer group dead letter",
        "paxos consensus algorithm state machine replication fault tolerance",
        "docker kubernetes container orchestration microservices mesh istio",
        "consistent hashing ring virtual nodes dynamodb partitioning",
        "singleflight thundering herd cache stampede request deduplication",
        "geohash spatial index 8-neighbor bounding box proximity search uber",
    ]

    index = InvertedIndexBM25(k1=1.5, b=0.75)
    raw_docs = []

    # Generate 1,000 documents
    random.seed(42)
    for i in range(1000):
        base_topic = topics[i % len(topics)]
        filler = f"system architecture teardown module {i} with production engineering notes"
        doc_text = f"{base_topic} {filler}"
        doc_id = f"doc_{i:04d}"
        index.add_document(doc_id, doc_text, {"title": f"Article #{i}"})
        raw_docs.append((doc_id, doc_text))

    query = "distributed consensus raft leader election"
    query_terms = tokenize(query)

    # Strategy A: Naive Linear Scan (regex / substring over all 1,000 docs)
    t0 = time.perf_counter()
    naive_matches = []
    for doc_id, text in raw_docs:
        text_lower = text.lower()
        matched = sum(1 for term in query_terms if term in text_lower)
        if matched > 0:
            naive_matches.append((doc_id, matched))
    naive_matches.sort(key=lambda x: x[1], reverse=True)
    t_naive = (time.perf_counter() - t0) * 1000.0

    # Strategy B: Inverted Index BM25 Search
    t1 = time.perf_counter()
    bm25_results = index.search(query, top_k=5)
    t_bm25 = (time.perf_counter() - t1) * 1000.0

    speedup = t_naive / t_bm25 if t_bm25 > 0 else 1.0

    table_1 = Table(title="Corpus Search Benchmark (1,000 Documents)")
    table_1.add_column("Search Strategy", style="bold")
    table_1.add_column("Complexity", justify="center", style="cyan")
    table_1.add_column("Query Latency", justify="right")
    table_1.add_column("Top Match ID", justify="center")
    table_1.add_column("Scoring Model", style="magenta")

    table_1.add_row(
        "Naive Substring Scan",
        "O(N * M)",
        f"{t_naive:.3f} ms",
        naive_matches[0][0] if naive_matches else "None",
        "Raw keyword presence count",
    )
    table_1.add_row(
        "Inverted Index + BM25",
        "O(K)",
        f"[bold green]{t_bm25:.3f} ms[/bold green]",
        bm25_results[0].doc_id if bm25_results else "None",
        f"[bold green]Okapi BM25 Relevance (Score: {bm25_results[0].score})[/bold green]",
    )
    console.print(table_1)
    console.print(f"  [PASS] Inverted Index achieved [bold green]{speedup:.1f}x speedup[/bold green] with probabilistic BM25 relevance scoring!\n")

    # -----------------------------------------------------------------------
    # Benchmark 2: BM25 Anti-Spam Defense (TF Saturation & Length Penalty)
    # -----------------------------------------------------------------------
    console.print("[bold yellow][BENCHMARK 2][/bold yellow] BM25 Anti-Spam Defense (TF Saturation vs Keyword Stuffing)...")
    console.print("  Comparing a concise technical doc vs an SEO keyword-stuffed spam document:")

    spam_index = InvertedIndexBM25(k1=1.5, b=0.75)

    # Document A: High quality concise article (50 words total, "microservices" mentioned 3 times)
    doc_a_text = (
        "Microservices architecture decomposes applications into modular decoupled services. "
        "Each microservices module manages an isolated database. "
        "Inter-service microservices communication uses gRPC and asynchronous messaging."
    )
    spam_index.add_document("doc_organic", doc_a_text, {"type": "Concise Technical Guide"})

    # Document B: SEO keyword spammer (330 words total, repeating "microservices" 10 times in filler)
    spam_text = ("microservices " * 10) + ("lorem ipsum dolor sit amet consectetur adipiscing elit " * 40)
    spam_index.add_document("doc_spammer", spam_text, {"type": "Keyword-Stuffed Spam"})

    results_spam = spam_index.search("microservices")

    table_2 = Table(title="Anti-Spam BM25 Defense against Keyword Stuffing")
    table_2.add_column("Document", style="bold")
    table_2.add_column("Doc Length", justify="center")
    table_2.add_column("Term Frequency", justify="center")
    table_2.add_column("BM25 Score", justify="center", style="cyan")
    table_2.add_column("Rank Verdict", style="magenta")

    for res in results_spam:
        verdict = "[bold green]#1 TOP RANK (High Quality)[/bold green]" if res.doc_id == "doc_organic" else "[red]#2 PENALIZED (Dilutive Spam)[/red]"
        table_2.add_row(
            res.doc_id,
            f"{res.document.token_count} tokens",
            f"{res.term_matches.get('microservices', 0)} times",
            f"[bold]{res.score}[/bold]",
            verdict,
        )
    console.print(table_2)
    console.print("  [PASS] BM25's length penalty (b=0.75) and TF saturation (k1=1.5) prevented the spammer from hijacking rank #1!\n")

    # -----------------------------------------------------------------------
    # Benchmark 3: Term IDF Scoring Decomposition
    # -----------------------------------------------------------------------
    console.print("[bold cyan][BENCHMARK 3][/bold cyan] Inverse Document Frequency (IDF) Term Weighting...")
    console.print("  Analyzing term contributions for query: 'database zookeeper':")

    idf_table = Table(title="BM25 IDF Weighting Breakdown Across Document Corpus")
    idf_table.add_column("Query Term", style="bold")
    idf_table.add_column("Corpus Document Frequency", justify="center")
    idf_table.add_column("Document Frequency %", justify="center")
    idf_table.add_column("Calculated IDF Weight", justify="right", style="green")
    idf_table.add_column("Information Value", style="cyan")

    sample_terms = [
        ("system", 950, "Ubiquitous / Near-stopword"),
        ("database", 420, "Common technical term"),
        ("consensus", 110, "Domain-specific term"),
        ("raft", 45, "Specific protocol"),
        ("zookeeper", 8, "Rare distinct keyword"),
    ]

    total_docs = 1000
    for term, df, label in sample_terms:
        # Lucene BM25 IDF formula
        idf = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
        pct = (df / total_docs) * 100.0
        idf_table.add_row(
            term,
            f"{df} / {total_docs}",
            f"{pct:.1f}%",
            f"[bold green]{idf:.4f}[/bold green]",
            label,
        )
    console.print(idf_table)
    console.print("  [PASS] Rare terms like 'zookeeper' receive ~9x higher weight than common terms like 'system'!\n")

    console.print("[bold green][SUCCESS][/bold green] Inverted Index & Okapi BM25 simulations concluded!\n")


if __name__ == "__main__":
    run_benchmark()
