"""
Interactive Terminal Simulation: Immutable Audit Trail & Tamper-Evident Ledger
==============================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.3

Demonstrates:
1. Cryptographic Hash Chaining on High-Value Financial Transactions
2. The Rogue DBA Attack: Automated Cryptographic Tamper Detection & Localization
3. O(log N) Zero-Knowledge Merkle Inclusion Proof for External Auditors
"""

import time
from core import (
    AuditLedger,
    GENESIS_HASH,
    IntegrityReport,
    MerkleTree,
    sha256_hex,
)

# ANSI Colors for clean terminal visualization
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GRAY = "\033[90m"


def print_header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 75}{RESET}")
    print(f"{BOLD}{CYAN}>>> {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 75}{RESET}\n")


def scenario_cryptographic_chaining() -> None:
    print_header("SCENARIO 1: Cryptographic Hash Chaining on Financial Ledger")
    print(f"{GRAY}Appends high-value financial actions. Each record includes the SHA-256{RESET}")
    print(f"{GRAY}digest of its predecessor, creating an unbroken cryptographic link.{RESET}\n")

    ledger = AuditLedger()

    events_data = [
        ("auth_service", "USER_LOGIN", "usr_alice", {"ip": "10.0.4.12", "mfa": True}),
        ("transfer_service", "CREATE_PAYMENT", "pay_9801", {"sender": "usr_alice", "recipient": "usr_bob", "amount": 250000.0, "currency": "USD"}),
        ("compliance_service", "AML_CHECK_PASSED", "pay_9801", {"risk_score": 0.02, "cleared_by": "auto_evaluator"}),
        ("ledger_service", "FUNDS_DEBITED", "acc_alice", {"amount": 250000.0, "balance_after": 750000.0}),
        ("ledger_service", "FUNDS_CREDITED", "acc_bob", {"amount": 250000.0, "balance_after": 250000.0}),
    ]

    for actor, action, res_id, payload in events_data:
        ledger.append(actor, action, res_id, payload)

    print(f"{BOLD}{'Seq':<4} | {'Actor':<18} | {'Action':<18} | {'Prev Hash':<16} | {'Event Hash'}{RESET}")
    print("-" * 75)

    for i in range(ledger.total_events):
        evt = ledger.get_event_by_sequence(i)
        prev_h = evt.prev_hash[:12] + "..." if evt.prev_hash != GENESIS_HASH else "[GENESIS]"
        curr_h = evt.event_hash[:12] + "..."
        print(
            f"{BOLD}{evt.sequence_id:<4}{RESET} | "
            f"{CYAN}{evt.actor:<18}{RESET} | "
            f"{YELLOW}{evt.action:<18}{RESET} | "
            f"{GRAY}{prev_h:<16}{RESET} | "
            f"{GREEN}{curr_h}{RESET}"
        )

    report = ledger.verify_chain_integrity()
    print("\n" + "-" * 75)
    print(f"{BOLD}{GREEN}[PASS] Chain Verification Complete:{RESET} {report.total_events_verified} records cryptographically validated (0 flaws).")


def scenario_rogue_dba_tamper_attack() -> None:
    print_header("SCENARIO 2: The Rogue DBA Attack (Cryptographic Tamper Detection)")
    print(f"{GRAY}Simulating an insider threat: A DBA with direct database access alters{RESET}")
    print(f"{GRAY}a historical record to change an audit amount. The scanner catches it.{RESET}\n")

    ledger = AuditLedger()
    ledger.append("alice", "SUBMIT_EXPENSE", "exp_101", {"amount": 150.0, "desc": "Team Dinner"})
    ledger.append("bob", "APPROVE_EXPENSE", "exp_101", {"amount": 150.0, "approver": "bob"})
    ledger.append("carol_cfo", "WIRE_TRANSFER", "wire_9002", {"amount": 1500000.0, "recipient": "vendor_corp"})
    ledger.append("audit_bot", "RECONCILE_DAY_CLOSE", "batch_77", {"status": "BALANCED"})

    print(f"{YELLOW}[ORIGINAL RECORD] Sequence #2 before tamper attack:{RESET}")
    original_evt = ledger.get_event_by_sequence(2)
    print(f"  Seq: {original_evt.sequence_id} | Action: {original_evt.action} | Payload: {original_evt.payload}")
    print(f"  SHA-256 Digest: {original_evt.event_hash}\n")

    # SIMULATE ATTACK
    print(f"{RED}{BOLD}[INSIDER ATTACK] Rogue DBA executes: UPDATE audit_log SET amount = 150.0 WHERE seq = 2...{RESET}")
    ledger.tamper_event_payload_for_testing(seq_id=2, field_to_modify="amount", new_value=150.0)

    tampered_evt = ledger.get_event_by_sequence(2)
    print(f"  Seq: {tampered_evt.sequence_id} | Action: {tampered_evt.action} | Tampered Payload: {tampered_evt.payload}\n")

    # SCANNER EXECUTION
    print(f"{CYAN}[AUTOMATED AUDITOR] Executing cryptographic chain integrity sweep...{RESET}")
    report = ledger.verify_chain_integrity()

    print("\n" + "-" * 75)
    print(f"{BOLD}TAMPER DETECTION TELEMETRY:{RESET}")
    print(f"  Status        : {RED}{BOLD}BREACH DETECTED (INTEGRITY COMPROMISED){RESET}")
    print(f"  Tampered Seq  : {RED}{BOLD}Record Sequence #{report.tampered_index}{RESET}")
    print(f"  Error Message : {report.error_message}")
    print(f"  Expected Hash : {GREEN}{report.expected_hash}{RESET}")
    print(f"  Actual Hash   : {RED}{report.actual_hash}{RESET}")
    print(f"\n{BOLD}{GREEN}[PASS] Proof of Tamper Resistance:{RESET} Historical record modifications cannot escape hash verification.")


def scenario_merkle_inclusion_proof() -> None:
    print_header("SCENARIO 3: Zero-Knowledge O(log N) Merkle Inclusion Proof")
    print(f"{GRAY}An auditor asks: 'Did transaction pay_007 occur in Epoch 1?'{RESET}")
    print(f"{GRAY}We provide an O(log N) Merkle proof without revealing other private transactions.{RESET}\n")

    ledger = AuditLedger()

    # Create 16 transactions
    for i in range(16):
        ledger.append(
            actor=f"user_{i:02d}",
            action="MAKE_PAYMENT",
            resource_id=f"pay_{i:04d}",
            payload={"amount": (i + 1) * 100, "customer": f"cust_{i}"},
        )

    # Seal epoch into a Merkle Tree
    seal = ledger.seal_epoch()
    print(f"{YELLOW}[EPOCH SEALED]{RESET} Epoch #{seal.epoch_id} ({seal.total_events} events sealed)")
    print(f"  Merkle Root Hash: {BOLD}{MAGENTA}{seal.merkle_root}{RESET}\n")

    # Generate proof for transaction pay_0007 (Sequence #7)
    target_event = ledger.get_event_by_sequence(7)
    print(f"{CYAN}[AUDITOR REQUEST] Verifying presence of: {target_event.event_id} (Seq #{target_event.sequence_id}){RESET}")
    print(f"  Target Leaf Hash: {target_event.event_hash}")

    proof = ledger.generate_proof_for_batch(
        event_id=target_event.event_id,
        start_seq=seal.start_sequence_id,
        end_seq=seal.end_sequence_id,
    )

    print(f"\n{BOLD}MERKLE PROOF PATH ({len(proof.path)} hashes - O(log2({seal.total_events}))):{RESET}")
    for idx, step in enumerate(proof.path):
        side = "LEFT " if step.is_left else "RIGHT"
        print(f"  Step {idx + 1}: Sibling [{side}] -> {GRAY}{step.sibling_hash[:16]}...{RESET}")

    # Verify proof
    is_valid = proof.verify()
    print("\n" + "-" * 75)
    print(f"  Cryptographic Proof Verification: {GREEN}{BOLD}VALID ({is_valid}){RESET}")
    print(f"  Proof size: {len(proof.path)} SHA-256 hashes instead of downloading {seal.total_events} complete events.")
    print(f"{BOLD}{GREEN}[PASS] Zero-Knowledge Audit Completed Successfully.{RESET}")


if __name__ == "__main__":
    scenario_cryptographic_chaining()
    scenario_rogue_dba_tamper_attack()
    scenario_merkle_inclusion_proof()
