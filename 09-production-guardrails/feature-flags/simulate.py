"""
Interactive Terminal Simulation: Feature Flags & Emergency Kill Switches
========================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.1

Demonstrates:
1. Progressive Percentage Rollouts & Monotonic Stickiness Invariant
2. Complex Multi-Attribute Dark Launch Targeting (Tenants, SemVer, Staff)
3. Live Incident Fire Drill: Concurrent Workers & Instantaneous O(1) Kill Switch
"""

import random
import threading
import time
from core import (
    EvaluationContext,
    EvaluationReason,
    FeatureFlag,
    FeatureFlagManager,
    Hasher,
    MultivariateVariant,
    RuleOperator,
    TargetingRule,
    Variation,
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


def scenario_progressive_rollout_stickiness() -> None:
    print_header("SCENARIO 1: Progressive Rollout & Monotonic Stickiness Invariant")
    print(f"{GRAY}Demonstrating that users assigned to an early rollout bucket (e.g. 15%){RESET}")
    print(f"{GRAY}NEVER oscillate or lose access when rollout expands to 30%, 60%, 100%.{RESET}\n")

    manager = FeatureFlagManager()
    flag = FeatureFlag(
        key="ai_recommendations_v2",
        enabled=True,
        percentage_rollout=0.0,
        rollout_variation=Variation("v2_deep_learning", True),
        off_variation=Variation("v1_collaborative_filtering", False),
    )
    manager.register_flag(flag)

    users = [f"user_{i:02d}" for i in range(1, 16)]
    rollout_stages = [0.0, 15.0, 40.0, 75.0, 100.0]

    # Pre-calculate user hash buckets
    print(f"{BOLD}{'User ID':<12} | {'Hash Bucket':<12} | " + " | ".join(f"{int(s)}% Stage" for s in rollout_stages) + f"{RESET}")
    print("-" * 75)

    user_evaluations: dict[str, list[bool]] = {u: [] for u in users}

    for stage in rollout_stages:
        manager.set_percentage_rollout("ai_recommendations_v2", stage)
        for u in users:
            ctx = EvaluationContext(u)
            enabled = manager.is_enabled("ai_recommendations_v2", ctx)
            user_evaluations[u].append(enabled)

    for u in users:
        bucket = Hasher.bucket("ai_recommendations_v2", u)
        eval_line = []
        for enabled in user_evaluations[u]:
            if enabled:
                eval_line.append(f"{GREEN}[ACTIVE]{RESET}")
            else:
                eval_line.append(f"{GRAY}[  --  ]{RESET}")
        print(f"{BOLD}{u:<12}{RESET} | {CYAN}bucket {bucket:02d}{RESET}  | " + " | ".join(eval_line))

    # Verify monotonic invariant across all users
    flips_detected = 0
    for u, history in user_evaluations.items():
        # Once True, it must never become False at a higher rollout stage
        seen_true = False
        for status in history:
            if status:
                seen_true = True
            elif seen_true and not status:
                flips_detected += 1

    print("\n" + "-" * 75)
    if flips_detected == 0:
        print(f"{BOLD}{GREEN}[PASS] Monotonic Invariant Verified:{RESET} 0 user oscillations detected across progressive stages.")
    else:
        print(f"{BOLD}{RED}[FAIL] Monotonic Invariant Broken:{RESET} {flips_detected} oscillations detected!")


def scenario_targeting_rules_dark_launch() -> None:
    print_header("SCENARIO 2: Targeted Dark Launch (Staff, VIP, & SemVer Rules)")
    print(f"{GRAY}Evaluating heterogeneous requests against sequential targeting rules{RESET}")
    print(f"{GRAY}before falling back to standard percentage rollout or default.{RESET}\n")

    staff_rule = TargetingRule(
        attribute_name="email",
        operator=RuleOperator.CONTAINS,
        target_value="@corp.internal",
        variation=Variation("internal_debug_ui", True),
    )
    enterprise_rule = TargetingRule(
        attribute_name="tenant_tier",
        operator=RuleOperator.EQUALS,
        target_value="ENTERPRISE",
        variation=Variation("enterprise_preview", True),
    )
    client_version_rule = TargetingRule(
        attribute_name="app_version",
        operator=RuleOperator.SEMVER_GTE,
        target_value="v3.4.0",
        variation=Variation("modern_react_native", True),
    )

    flag = FeatureFlag(
        key="new_navigation_bar",
        enabled=True,
        targeting_rules=[staff_rule, enterprise_rule, client_version_rule],
        percentage_rollout=20.0,
        rollout_variation=Variation("canary_cohort", True),
        default_variation=Variation("legacy_nav", False),
    )
    manager = FeatureFlagManager()
    manager.register_flag(flag)

    test_contexts = [
        EvaluationContext("usr_alice", {"email": "alice@corp.internal", "app_version": "v3.0.0"}),
        EvaluationContext("usr_bob", {"email": "bob@acme.com", "tenant_tier": "ENTERPRISE", "app_version": "v2.9.0"}),
        EvaluationContext("usr_charlie", {"email": "charlie@gmail.com", "tenant_tier": "FREE", "app_version": "v3.5.2"}),
        EvaluationContext("usr_david", {"email": "david@gmail.com", "tenant_tier": "FREE", "app_version": "v2.1.0"}),
        EvaluationContext("usr_eve", {"email": "eve@yahoo.com", "tenant_tier": "FREE", "app_version": "v1.0.0"}),
    ]

    print(f"{BOLD}{'Entity':<12} | {'Attributes':<35} | {'Matched Reason':<20} | {'Served Variation'}{RESET}")
    print("-" * 88)

    for ctx in test_contexts:
        res = manager.evaluate("new_navigation_bar", ctx)
        attr_summary = ", ".join(f"{k}={v}" for k, v in ctx.attributes.items())
        if len(attr_summary) > 33:
            attr_summary = attr_summary[:30] + "..."

        if res.reason == EvaluationReason.TARGETING_MATCH:
            reason_color = GREEN
        elif res.reason == EvaluationReason.PERCENTAGE_ROLLOUT:
            reason_color = CYAN
        else:
            reason_color = GRAY

        print(
            f"{BOLD}{ctx.entity_id:<12}{RESET} | "
            f"{attr_summary:<35} | "
            f"{reason_color}{res.reason.value:<20}{RESET} | "
            f"{MAGENTA}{res.variation.name}{RESET}"
        )


def scenario_live_fire_drill_kill_switch() -> None:
    print_header("SCENARIO 3: Live Incident Fire Drill (O(1) Emergency Kill Switch)")
    print(f"{GRAY}Simulating high-throughput traffic across 10 concurrent worker threads.{RESET}")
    print(f"{GRAY}An error spike trips the emergency kill switch in flight.{RESET}\n")

    manager = FeatureFlagManager()
    flag = FeatureFlag(
        key="payment_crypto_checkout",
        enabled=True,
        percentage_rollout=100.0,
        default_variation=Variation("crypto_gateway_v2", True),
        off_variation=Variation("legacy_credit_card_v1", False),
    )
    manager.register_flag(flag)

    stop_event = threading.Event()
    traffic_stats = {"before_kill": {"v2": 0, "fallback": 0}, "after_kill": {"v2": 0, "fallback": 0}}
    kill_switched = False
    lock = threading.Lock()

    def worker_thread(worker_id: int):
        req_count = 0
        while not stop_event.is_set():
            ctx = EvaluationContext(f"req_{worker_id}_{req_count}")
            res = manager.evaluate("payment_crypto_checkout", ctx)
            with lock:
                phase = "after_kill" if kill_switched else "before_kill"
                if res.value:
                    traffic_stats[phase]["v2"] += 1
                else:
                    traffic_stats[phase]["fallback"] += 1
            req_count += 1
            time.sleep(0.002)

    workers = [threading.Thread(target=worker_thread, args=(i,)) for i in range(10)]
    for w in workers:
        w.start()

    print(f"{YELLOW}[PHASE 1] Feature flag active at 100% rollout. Processing live checkout traffic...{RESET}")
    time.sleep(0.3)

    print(f"\n{RED}{BOLD}[ALERT] 502 Bad Gateway spike detected on Crypto Gateway downstream partner!{RESET}")
    print(f"{RED}{BOLD}[ACTION] On-Call SRE executes emergency kill switch CLI command...{RESET}")

    manager.emergency_kill_switch(
        flag_key="payment_crypto_checkout",
        actor="oncall_sre@corp.internal",
        reason="Partner API outage (502 HTTP Bad Gateway)",
    )
    with lock:
        kill_switched = True

    print(f"{GREEN}[PHASE 2] Kill switch tripped in O(1) time. Monitoring traffic pivot...{RESET}")
    time.sleep(0.3)

    stop_event.set()
    for w in workers:
        w.join()

    print("\n" + "-" * 75)
    print(f"{BOLD}FIRE DRILL RESULTS TELEMETRY:{RESET}")
    print(f"  Before Kill Switch: {GREEN}{traffic_stats['before_kill']['v2']} requests routed to Crypto v2{RESET}, {traffic_stats['before_kill']['fallback']} to fallback.")
    print(f"  After Kill Switch : {traffic_stats['after_kill']['v2']} requests routed to Crypto v2, {RED}{BOLD}{traffic_stats['after_kill']['fallback']} requests instantly protected via fallback.{RESET}")

    print("\n" + f"{BOLD}AUDIT LOG TRAIL RECORDED:{RESET}")
    for record in manager.get_audit_log("payment_crypto_checkout"):
        print(f"  - [{time.strftime('%H:%M:%S', time.localtime(record.timestamp))}] {YELLOW}{record.action:<22}{RESET} by {CYAN}{record.actor:<24}{RESET} -> {record.details}")


if __name__ == "__main__":
    scenario_progressive_rollout_stickiness()
    scenario_targeting_rules_dark_launch()
    scenario_live_fire_drill_kill_switch()
