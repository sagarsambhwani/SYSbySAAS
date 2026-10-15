"""Unit, Information Retrieval Invariant, and Relevance Tests for Inverted Index & BM25."""

import pytest

from core import (
    InvertedIndexBM25,
    tokenize,
)


class TestTokenizer:
    def test_tokenization_and_stopwords(self):
        text = "The quick brown fox jumps over the lazy dog!"
        tokens = tokenize(text)
        assert "the" not in tokens
        assert "over" not in tokens
        assert "quick" in tokens
        assert "fox" in tokens
        assert "dog" in tokens

    def test_special_chars_and_case(self):
        tokens = tokenize("Elasticsearch-8.0 & Apache_Lucene distributed-system!")
        assert "elasticsearch-8" in tokens or "elasticsearch" in tokens[0]
        assert "apache_lucene" in tokens


class TestInvertedIndexBasics:
    def test_indexing_and_postings(self):
        index = InvertedIndexBM25()
        index.add_document("doc1", "Distributed consensus with Raft and Paxos")
        index.add_document("doc2", "Paxos algorithm for state machine replication")

        assert index.total_documents == 2
        assert "paxos" in index._index
        assert len(index._index["paxos"]) == 2  # Appears in both doc1 and doc2
        assert len(index._index["raft"]) == 1   # Appears only in doc1

    def test_deletion_and_update(self):
        index = InvertedIndexBM25()
        index.add_document("d1", "Redis in-memory cache")
        index.add_document("d2", "Memcached distributed cache")
        assert index.total_documents == 2

        # Delete d1
        assert index.delete_document("d1") is True
        assert index.total_documents == 1
        assert "redis" not in index._index
        assert len(index._index["cache"]) == 1  # Only d2 left

        # Search for deleted term returns nothing
        results = index.search("redis")
        assert len(results) == 0


class TestBM25RankingInvariants:
    def test_idf_weights_rare_terms_higher(self):
        """A document matching a rare term must score higher than a document matching a ubiquitous term."""
        index = InvertedIndexBM25()

        # "database" appears in all 3 docs; "zookeeper" appears in only 1 doc
        index.add_document("doc_common_1", "Relational database SQL storage")
        index.add_document("doc_common_2", "NoSQL database key value store")
        index.add_document("doc_rare", "Apache ZooKeeper coordination service for database clusters")

        # Query: "database zookeeper"
        results = index.search("database zookeeper")

        assert len(results) >= 1
        # doc_rare MUST rank #1 because "zookeeper" has far higher IDF than "database"
        assert results[0].doc_id == "doc_rare"
        assert results[0].term_matches.get("zookeeper", 0) > 0

    def test_document_length_normalization(self):
        """Short concise documents with the keyword must rank higher than verbose dilutive documents."""
        index = InvertedIndexBM25(b=0.75)

        # Short document: 3 tokens total
        index.add_document("short_doc", "Kafka message broker")

        # Verbose document: 50 words containing filler, with "kafka" mentioned once
        filler = "lorem ipsum dolor sit amet " * 8
        index.add_document("long_doc", f"{filler} Kafka message queue infrastructure setup guide")

        results = index.search("kafka")
        assert len(results) == 2
        # short_doc has higher term density -> must rank higher than long_doc
        assert results[0].doc_id == "short_doc"
        assert results[1].doc_id == "long_doc"
        assert results[0].score > results[1].score

    def test_term_frequency_saturation(self):
        """Repeating a word 20 times does NOT give a 20x higher score due to BM25 k1 saturation."""
        index = InvertedIndexBM25(k1=1.2, b=0.0)  # b=0 disables length penalty to isolate TF saturation

        index.add_document("doc_moderate", "Docker containerization deployment")
        index.add_document("doc_stuffed", "Docker Docker Docker Docker Docker Docker Docker Docker Docker Docker container")

        results = index.search("docker")
        assert len(results) == 2

        score_moderate = next(r.score for r in results if r.doc_id == "doc_moderate")
        score_stuffed = next(r.score for r in results if r.doc_id == "doc_stuffed")

        # Under naive TF, stuffed would be 10x higher.
        # Under BM25 with k1=1.2, the ratio (score_stuffed / score_moderate) is strictly bounded < 2.0!
        tf_ratio = score_stuffed / score_moderate
        assert tf_ratio < 2.0


class TestSearchEdgeCases:
    def test_empty_and_stopwords_only_queries(self):
        index = InvertedIndexBM25()
        index.add_document("d1", "Cassandra distributed database")

        assert index.search("") == []
        assert index.search("the and is") == []
        assert index.search("nonexistentterm12345") == []
