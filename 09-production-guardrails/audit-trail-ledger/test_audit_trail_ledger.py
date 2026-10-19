"""
Unit & Cryptographic Invariant Tests for Immutable Audit Ledger
================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.3
"""

import threading
import time
import pytest
from core import (
    AuditEvent,
    AuditLedger,
    GENESIS_HASH,
    IntegrityReport,
    MerkleProof,
    MerkleTree,
    sha256_hex,
)


class TestAuditChaining:
    def test_genesis_and_hash_chain_linkage(self):
        ledger = AuditLedger()
        e0 = ledger.append("admin", "LOGIN", "auth_service", {"ip": "192.168.1.1"})
        assert e0.sequence_id == 0
        assert e0.prev_hash == GENESIS_HASH
        assert len(e0.event_hash) == 64

        e1 = ledger.append("admin", "CREATE_USER", "user_service", {"username": "bob"})
        assert e1.sequence_id == 1
        assert e1.prev_hash == e0.event_hash
        assert len(e1.event_hash) == 64

        e2 = ledger.append("bob", "UPDATE_PASSWORD", "user_service", {"success": True})
        assert e2.sequence_id == 2
        assert e2.prev_hash == e1.event_hash

        # Verify integrity
        report = ledger.verify_chain_integrity()
        assert report.is_valid is True
        assert report.total_events_verified == 3


class TestTamperDetection:
    def test_payload_tampering_detection(self):
        """
        Simulate rogue DBA modifying a historical transaction amount.
        The cryptographic integrity scanner must detect and pinpoint the exact record.
        """
        ledger = AuditLedger()
        ledger.append("alice", "WIRE_TRANSFER", "account_123", {"amount": 500.0, "currency": "USD"})
        ledger.append("alice", "WIRE_TRANSFER", "account_123", {"amount": 1000000.0, "currency": "USD"})
        ledger.append("alice", "LOGOUT", "auth_service", {})

        # Verify healthy baseline
        assert ledger.verify_chain_integrity().is_valid is True

        # Rogue DBA modifies event 1 ($1,000,000 -> $10.0)
        tampered = ledger.tamper_event_payload_for_testing(1, "amount", 10.0)
        assert tampered is True

        # Verify tamper detection
        report = ledger.verify_chain_integrity()
        assert report.is_valid is False
        assert report.tampered_index == 1
        assert report.total_events_verified == 1
        assert "internal fields do not match cryptographic digest" in report.error_message

    def test_hash_recalculation_tamper_attack(self):
        """
        If a rogue attacker recomputes the event_hash of the tampered event,
        it will break the prev_hash link of event 2!
        """
        ledger = AuditLedger()
        ledger.append("u1", "ACTION_0", "res_0")
        ledger.append("u1", "ACTION_1", "res_1", {"data": "original"})
        ledger.append("u1", "ACTION_2", "res_2")

        # Mutate event 1 AND its event_hash
        original = ledger._events[1]
        tampered_event = AuditEvent(
            sequence_id=original.sequence_id,
            event_id=original.event_id,
            timestamp=original.timestamp,
            actor=original.actor,
            action=original.action,
            resource_id=original.resource_id,
            payload={"data": "tampered"},
            prev_hash=original.prev_hash,
            event_hash="f" * 64,  # Arbitrary recalculated hash
        )
        ledger._events[1] = tampered_event

        report = ledger.verify_chain_integrity()
        assert report.is_valid is False
        assert report.tampered_index == 1


class TestMerkleTreeAndInclusionProofs:
    def test_merkle_tree_root_and_inclusion_proof(self):
        """
        Verify that O(log N) inclusion proofs mathematically verify against the Merkle Root.
        """
        hashes = [sha256_hex(f"leaf_data_{i}") for i in range(16)]
        tree = MerkleTree(hashes)

        assert len(tree.root) == 64
        assert len(tree.levels) == 5  # 16 -> 8 -> 4 -> 2 -> 1 (log2(16) + 1)

        # Generate and verify proof for every single leaf
        for i in range(16):
            proof = tree.get_inclusion_proof(i)
            assert proof.leaf_hash == hashes[i]
            assert proof.root_hash == tree.root
            assert len(proof.path) == 4  # log2(16)
            assert proof.verify() is True

    def test_tampered_merkle_proof_rejected(self):
        hashes = [sha256_hex(f"leaf_{i}") for i in range(8)]
        tree = MerkleTree(hashes)

        proof = tree.get_inclusion_proof(3)
        assert proof.verify() is True

        # Tamper with leaf hash in proof
        tampered_proof = MerkleProof(
            leaf_hash=sha256_hex("fake_leaf_data"),
            root_hash=proof.root_hash,
            path=proof.path,
        )
        assert tampered_proof.verify() is False

    def test_odd_number_of_leaves_handled(self):
        hashes = [sha256_hex(f"leaf_{i}") for i in range(5)]
        tree = MerkleTree(hashes)

        for i in range(5):
            proof = tree.get_inclusion_proof(i)
            assert proof.verify() is True


class TestEpochSealing:
    def test_batch_epoch_sealing(self):
        ledger = AuditLedger()
        for i in range(10):
            ledger.append("system", f"HEARTBEAT_{i}", "server_1")

        # Seal first epoch of 10 events
        seal_1 = ledger.seal_epoch()
        assert seal_1 is not None
        assert seal_1.epoch_id == 1
        assert seal_1.start_sequence_id == 0
        assert seal_1.end_sequence_id == 9
        assert seal_1.total_events == 10
        assert len(seal_1.merkle_root) == 64

        # Calling seal again with no new events returns None
        assert ledger.seal_epoch() is None

        # Add 5 more events and seal second epoch
        for i in range(5):
            ledger.append("system", f"NEW_EVENT_{i}", "server_2")

        seal_2 = ledger.seal_epoch()
        assert seal_2 is not None
        assert seal_2.epoch_id == 2
        assert seal_2.start_sequence_id == 10
        assert seal_2.end_sequence_id == 14
        assert seal_2.total_events == 5


class TestConcurrentAppends:
    def test_concurrent_appends_maintain_clean_chain(self):
        """
        Verify that multiple concurrent worker threads appending events
        produce a strictly monotonic, unbroken SHA-256 chain.
        """
        ledger = AuditLedger()
        n_threads = 8
        events_per_thread = 50

        def worker(thread_id: int):
            for i in range(events_per_thread):
                ledger.append(
                    actor=f"worker_{thread_id}",
                    action="PROCESS_BATCH",
                    resource_id=f"job_{thread_id}_{i}",
                    payload={"iteration": i},
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert ledger.total_events == n_threads * events_per_thread

        # Verify unbroken cryptographic chain across all 400 events
        report = ledger.verify_chain_integrity()
        assert report.is_valid is True
        assert report.total_events_verified == 400
