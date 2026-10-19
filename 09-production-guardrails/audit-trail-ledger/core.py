"""
Immutable Audit Trail & Tamper-Evident Ledger Engine
====================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.3

Production-grade implementation of an append-only, tamper-evident audit ledger
inspired by AWS CloudTrail, Amazon QLDB, Git commit chains, and Certificate
Transparency logs (RFC 6962).

Key Architecture Principles:
1. Cryptographic Hash Chaining: Every event includes the SHA-256 digest of
   the preceding event, guaranteeing that history cannot be rewritten or mutated.
2. Merkle Tree Batch Epochs: Batches of audit events are sealed into binary
   Merkle trees, generating a single immutable cryptographic Merkle Root.
3. O(log N) Cryptographic Inclusion Proofs: External auditors or customers can
   cryptographically verify that their event exists in the ledger without
   requiring access to the rest of the sensitive audit database.
4. Tamper Detection & Localization: Automated integrity scanner that pinpoints
   the exact record index and cryptographic divergence during tampering.
5. Thread-Safe Concurrency: Strictly serialized monotonic sequence numbering
   and atomic chain linkage under high concurrency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AuditLedger")

GENESIS_HASH = "0" * 64


def sha256_hex(data: bytes | str) -> str:
    """Computes standard SHA-256 hexadecimal digest."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    """An immutable, cryptographically chained audit record."""
    sequence_id: int
    event_id: str
    timestamp: float
    actor: str
    action: str
    resource_id: str
    payload: Dict[str, Any]
    prev_hash: str
    event_hash: str

    def compute_expected_hash(self) -> str:
        """
        Computes the canonical hash of this event:
        H_i = SHA-256(seq_id || timestamp || actor || action || resource_id || canonical_payload || prev_hash)
        """
        payload_json = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        raw = f"{self.sequence_id}|{self.timestamp:.6f}|{self.actor}|{self.action}|{self.resource_id}|{payload_json}|{self.prev_hash}"
        return sha256_hex(raw)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MerkleProofStep:
    """A single step in a Merkle inclusion proof."""
    sibling_hash: str
    is_left: bool


@dataclass
class MerkleProof:
    """Cryptographic proof of inclusion in a Merkle tree (O(log N) size)."""
    leaf_hash: str
    root_hash: str
    path: List[MerkleProofStep]

    def verify(self) -> bool:
        """Recomputes root from leaf hash and proof path."""
        current_hash = self.leaf_hash
        for step in self.path:
            if step.is_left:
                combined = step.sibling_hash + current_hash
            else:
                combined = current_hash + step.sibling_hash
            current_hash = sha256_hex(combined)
        return current_hash == self.root_hash


class MerkleTree:
    """
    Binary Merkle Tree constructed over a batch of audit event hashes.
    Provides O(log N) inclusion proofs.
    """
    def __init__(self, leaf_hashes: List[str]) -> None:
        if not leaf_hashes:
            self.leaf_hashes: List[str] = [GENESIS_HASH]
        else:
            self.leaf_hashes = list(leaf_hashes)

        self.levels: List[List[str]] = [self.leaf_hashes]
        self._build_tree()

    def _build_tree(self) -> None:
        current_level = self.leaf_hashes
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                combined_hash = sha256_hex(left + right)
                next_level.append(combined_hash)
            self.levels.append(next_level)
            current_level = next_level

    @property
    def root(self) -> str:
        return self.levels[-1][0]

    def get_inclusion_proof(self, leaf_index: int) -> MerkleProof:
        if leaf_index < 0 or leaf_index >= len(self.leaf_hashes):
            raise IndexError("Leaf index out of bounds")

        path: List[MerkleProofStep] = []
        idx = leaf_index

        for level in self.levels[:-1]:
            is_right_child = (idx % 2 == 1)
            sibling_idx = idx - 1 if is_right_child else idx + 1

            if sibling_idx >= len(level):
                # Odd node duplicated
                sibling_hash = level[idx]
            else:
                sibling_hash = level[sibling_idx]

            path.append(MerkleProofStep(sibling_hash=sibling_hash, is_left=is_right_child))
            idx //= 2

        return MerkleProof(
            leaf_hash=self.leaf_hashes[leaf_index],
            root_hash=self.root,
            path=path,
        )


@dataclass
class EpochSeal:
    """A sealed batch of events with its cryptographic Merkle Root."""
    epoch_id: int
    start_sequence_id: int
    end_sequence_id: int
    merkle_root: str
    sealed_at: float
    total_events: int


@dataclass
class IntegrityReport:
    """Comprehensive health and tampering diagnostic report."""
    is_valid: bool
    total_events_verified: int
    tampered_index: Optional[int] = None
    expected_hash: Optional[str] = None
    actual_hash: Optional[str] = None
    error_message: Optional[str] = None


class AuditLedger:
    """
    Append-only, cryptographically verifiable audit trail engine.
    Ensures that historical actions cannot be silently altered or deleted.
    """
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: List[AuditEvent] = []
        self._event_index_by_id: Dict[str, int] = {}
        self._epochs: List[EpochSeal] = []
        self._last_sealed_index: int = -1

    def append(
        self,
        actor: str,
        action: str,
        resource_id: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> AuditEvent:
        """
        Appends an event to the ledger, computing the SHA-256 chain link atomically.
        """
        payload = payload or {}
        now = time.time()

        with self._lock:
            seq_id = len(self._events)
            prev_hash = self._events[-1].event_hash if seq_id > 0 else GENESIS_HASH
            evt_id = event_id or f"evt_{seq_id:08d}_{sha256_hex(str(now))[:8]}"

            # Construct unhashed event
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            raw = f"{seq_id}|{now:.6f}|{actor}|{action}|{resource_id}|{payload_json}|{prev_hash}"
            event_hash = sha256_hex(raw)

            event = AuditEvent(
                sequence_id=seq_id,
                event_id=evt_id,
                timestamp=now,
                actor=actor,
                action=action,
                resource_id=resource_id,
                payload=payload,
                prev_hash=prev_hash,
                event_hash=event_hash,
            )

            self._events.append(event)
            self._event_index_by_id[evt_id] = seq_id
            return event

    def seal_epoch(self) -> Optional[EpochSeal]:
        """
        Seals all unsealed events into a Merkle Tree, producing a verifiable epoch seal.
        """
        with self._lock:
            start_idx = self._last_sealed_index + 1
            if start_idx >= len(self._events):
                return None  # No new events to seal

            batch = self._events[start_idx:]
            leaf_hashes = [e.event_hash for e in batch]
            tree = MerkleTree(leaf_hashes)

            epoch_id = len(self._epochs) + 1
            seal = EpochSeal(
                epoch_id=epoch_id,
                start_sequence_id=batch[0].sequence_id,
                end_sequence_id=batch[-1].sequence_id,
                merkle_root=tree.root,
                sealed_at=time.time(),
                total_events=len(batch),
            )
            self._epochs.append(seal)
            self._last_sealed_index = len(self._events) - 1
            return seal

    def verify_chain_integrity(self) -> IntegrityReport:
        """
        Full cryptographic verification of the hash chain.
        Scans all records from 0 to N-1, verifying:
        1. prev_hash matches preceding event's hash.
        2. event_hash matches the SHA-256 of the record fields.
        Returns a report pointing to the exact point of tampering.
        """
        with self._lock:
            if not self._events:
                return IntegrityReport(is_valid=True, total_events_verified=0)

            for i, event in enumerate(self._events):
                # 1. Verify prev_hash link
                expected_prev = self._events[i - 1].event_hash if i > 0 else GENESIS_HASH
                if event.prev_hash != expected_prev:
                    return IntegrityReport(
                        is_valid=False,
                        total_events_verified=i,
                        tampered_index=i,
                        expected_hash=expected_prev,
                        actual_hash=event.prev_hash,
                        error_message=f"Broken hash chain at sequence {i}: prev_hash does not match preceding record",
                    )

                # 2. Verify payload and event hash integrity
                expected_hash = event.compute_expected_hash()
                if event.event_hash != expected_hash:
                    return IntegrityReport(
                        is_valid=False,
                        total_events_verified=i,
                        tampered_index=i,
                        expected_hash=expected_hash,
                        actual_hash=event.event_hash,
                        error_message=f"Tampered record at sequence {i}: internal fields do not match cryptographic digest",
                    )

            return IntegrityReport(is_valid=True, total_events_verified=len(self._events))

    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        with self._lock:
            idx = self._event_index_by_id.get(event_id)
            if idx is not None and idx < len(self._events):
                return self._events[idx]
            return None

    def get_event_by_sequence(self, seq_id: int) -> Optional[AuditEvent]:
        with self._lock:
            if 0 <= seq_id < len(self._events):
                return self._events[seq_id]
            return None

    def generate_proof_for_batch(
        self,
        event_id: str,
        start_seq: int,
        end_seq: int,
    ) -> MerkleProof:
        """
        Generates an O(log N) Merkle inclusion proof for an event within a specified range.
        """
        with self._lock:
            target_idx = self._event_index_by_id.get(event_id)
            if target_idx is None or not (start_seq <= target_idx <= end_seq):
                raise ValueError("Event not in specified sequence range")

            batch = self._events[start_seq : end_seq + 1]
            leaf_hashes = [e.event_hash for e in batch]
            tree = MerkleTree(leaf_hashes)

            batch_relative_idx = target_idx - start_seq
            return tree.get_inclusion_proof(batch_relative_idx)

    def tamper_event_payload_for_testing(
        self,
        seq_id: int,
        field_to_modify: str,
        new_value: Any,
    ) -> bool:
        """
        Simulates an insider threat or database tampering event (e.g. rogue DBA
        issuing an UPDATE query directly on the underlying audit table).
        """
        with self._lock:
            if seq_id < 0 or seq_id >= len(self._events):
                return False

            original = self._events[seq_id]
            tampered_payload = dict(original.payload)
            tampered_payload[field_to_modify] = new_value

            # Mutate event behind the engine's back
            tampered = AuditEvent(
                sequence_id=original.sequence_id,
                event_id=original.event_id,
                timestamp=original.timestamp,
                actor=original.actor,
                action=original.action,
                resource_id=original.resource_id,
                payload=tampered_payload,
                prev_hash=original.prev_hash,
                event_hash=original.event_hash,  # Retain original hash to simulate stealth edit
            )
            self._events[seq_id] = tampered
            return True

    @property
    def total_events(self) -> int:
        with self._lock:
            return len(self._events)
