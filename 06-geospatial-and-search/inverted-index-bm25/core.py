"""Inverted Index & Okapi BM25 Relevance Ranking Engine (Elasticsearch / Lucene Core).

Implements:
1. Tokenizer: Lowercasing, regex token extraction, and stopword filtering.
2. Inverted Index: Term-to-posting-list dictionary with document IDs and term frequencies.
3. Okapi BM25 Ranking: Modern information retrieval scoring with term frequency saturation (k1)
   and document length normalization (b).
"""

from collections import Counter
from dataclasses import dataclass, field
import math
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

# Standard English stopwords
DEFAULT_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "could", "did", "do", "does", "doing", "down",
    "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "whom", "why", "with", "would", "you", "your", "yours", "yourself",
}


def tokenize(text: str, remove_stopwords: bool = True) -> List[str]:
    """Extracts lowercase alphanumeric tokens and filters stopwords."""
    words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    if remove_stopwords:
        return [w for w in words if w not in DEFAULT_STOPWORDS and len(w) > 1]
    return words


@dataclass
class Posting:
    """Entry in a term's posting list."""
    doc_id: str
    term_freq: int


@dataclass
class Document:
    doc_id: str
    text: str
    token_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    doc_id: str
    score: float
    document: Document
    term_matches: Dict[str, int]


class InvertedIndexBM25:
    """Production-grade Inverted Index with Okapi BM25 relevance ranking."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """
        Args:
            k1: Term frequency saturation constant (typically 1.2 to 2.0).
            b: Document length normalization constant (typically 0.75).
        """
        self.k1 = k1
        self.b = b

        # Term -> List[Posting]
        self._index: Dict[str, List[Posting]] = {}
        # Document store
        self._doc_store: Dict[str, Document] = {}
        # Document length (token count)
        self._doc_lengths: Dict[str, int] = {}
        self._total_tokens = 0
        self._lock = threading.Lock()

    def add_document(self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Indexes a document into the inverted index."""
        tokens = tokenize(text)
        token_count = len(tokens)
        term_counts = Counter(tokens)

        with self._lock:
            # If doc exists, delete old version first
            if doc_id in self._doc_store:
                self._delete_doc_internal(doc_id)

            doc = Document(doc_id=doc_id, text=text, token_count=token_count, metadata=metadata or {})
            self._doc_store[doc_id] = doc
            self._doc_lengths[doc_id] = token_count
            self._total_tokens += token_count

            for term, count in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append(Posting(doc_id=doc_id, term_freq=count))

    def _delete_doc_internal(self, doc_id: str) -> None:
        old_doc = self._doc_store.pop(doc_id, None)
        if old_doc:
            old_len = self._doc_lengths.pop(doc_id, 0)
            self._total_tokens -= old_len
            # Prune doc from postings
            for term in list(self._index.keys()):
                self._index[term] = [p for p in self._index[term] if p.doc_id != doc_id]
                if not self._index[term]:
                    del self._index[term]

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            if doc_id in self._doc_store:
                self._delete_doc_internal(doc_id)
                return True
            return False

    @property
    def total_documents(self) -> int:
        with self._lock:
            return len(self._doc_store)

    @property
    def avg_doc_length(self) -> float:
        with self._lock:
            if not self._doc_store:
                return 0.0
            return self._total_tokens / len(self._doc_store)

    def compute_idf(self, term: str) -> float:
        """Calculates Lucene/BM25 Inverse Document Frequency (IDF).

        Formula:
            IDF(q) = ln( 1 + (N - n(q) + 0.5) / (n(q) + 0.5) )
        """
        N = len(self._doc_store)
        n = len(self._index.get(term, []))
        if n == 0 or N == 0:
            return 0.0
        return math.log(1.0 + (N - n + 0.5) / (n + 0.5))

    def search(
        self,
        query: str,
        top_k: int = 10,
        custom_k1: Optional[float] = None,
        custom_b: Optional[float] = None,
    ) -> List[SearchResult]:
        """Executes full-text search with Okapi BM25 scoring.

        BM25 Formula:
            Score(D, Q) = SUM_q [ IDF(q) * (TF * (k1 + 1)) / (TF + k1 * (1 - b + b * (|D| / avgdl))) ]
        """
        query_terms = tokenize(query)
        if not query_terms:
            return []

        k1 = custom_k1 if custom_k1 is not None else self.k1
        b = custom_b if custom_b is not None else self.b

        with self._lock:
            if not self._doc_store:
                return []

            N = len(self._doc_store)
            avgdl = self._total_tokens / N if N > 0 else 1.0

            # Step 1: Identify all candidate documents containing at least one query term
            candidate_docs: Set[str] = set()
            term_postings: Dict[str, Dict[str, int]] = {}

            for term in query_terms:
                postings = self._index.get(term, [])
                term_postings[term] = {p.doc_id: p.term_freq for p in postings}
                candidate_docs.update(term_postings[term].keys())

            # Step 2: Score each candidate document using Okapi BM25
            scores: Dict[str, float] = {}
            term_matches: Dict[str, Dict[str, int]] = {doc_id: {} for doc_id in candidate_docs}

            for term in set(query_terms):
                if term not in term_postings or not term_postings[term]:
                    continue

                idf = self.compute_idf(term)
                posting_map = term_postings[term]

                for doc_id, tf in posting_map.items():
                    doc_len = self._doc_lengths[doc_id]

                    # BM25 Term Frequency saturation & length normalization formula
                    numerator = tf * (k1 + 1.0)
                    denominator = tf + k1 * (1.0 - b + b * (doc_len / avgdl))
                    term_score = idf * (numerator / denominator)

                    scores[doc_id] = scores.get(doc_id, 0.0) + term_score
                    term_matches[doc_id][term] = tf

            # Step 3: Sort by score descending and return top_k
            ranked_doc_ids = sorted(scores.keys(), key=lambda d: scores[d], reverse=True)[:top_k]

            results = [
                SearchResult(
                    doc_id=doc_id,
                    score=round(scores[doc_id], 4),
                    document=self._doc_store[doc_id],
                    term_matches=term_matches[doc_id],
                )
                for doc_id in ranked_doc_ids
            ]
            return results

    def get_document(self, doc_id: str) -> Optional[Document]:
        with self._lock:
            return self._doc_store.get(doc_id)
