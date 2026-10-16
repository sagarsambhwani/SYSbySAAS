"""
Unit & Concurrency Tests for Feature Flags & Emergency Kill Switches
=====================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.1
"""

import threading
import time
import pytest
from core import (
    AuditRecord,
    EvaluationContext,
    EvaluationReason,
    EvaluationResult,
    FeatureFlag,
    FeatureFlagManager,
    Hasher,
    MultivariateVariant,
    RuleOperator,
    TargetingRule,
    Variation,
)


class TestBasicEvaluation:
    def test_missing_flag_fails_closed(self):
        manager = FeatureFlagManager()
        ctx = EvaluationContext(entity_id="user_1")
        res = manager.evaluate("non_existent_flag", ctx, fallback_value=False)
        assert res.enabled is False
        assert res.value is False
        assert res.reason == EvaluationReason.FLAG_NOT_FOUND

    def test_globally_disabled_flag(self):
        manager = FeatureFlagManager()
        flag = FeatureFlag(
            key="new_checkout_flow",
            enabled=False,
            default_variation=Variation("v2", True),
            off_variation=Variation("v1_legacy", False),
        )
        manager.register_flag(flag)

        ctx = EvaluationContext(entity_id="user_1")
        res = manager.evaluate("new_checkout_flow", ctx)
        assert res.enabled is False
        assert res.value is False
        assert res.reason == EvaluationReason.DISABLED
        assert res.variation.name == "v1_legacy"


class TestTargetingRules:
    def test_user_id_in_list_rule(self):
        manager = FeatureFlagManager()
        beta_rule = TargetingRule(
            attribute_name="entity_id",
            operator=RuleOperator.IN_LIST,
            target_value=["vip_user_1", "vip_user_2"],
            variation=Variation("premium_ai_feature", True),
        )
        flag = FeatureFlag(
            key="ai_assistant",
            enabled=True,
            targeting_rules=[beta_rule],
            default_variation=Variation("standard", False),
        )
        manager.register_flag(flag)

        # VIP user matches rule
        ctx_vip = EvaluationContext(entity_id="vip_user_1")
        res_vip = manager.evaluate("ai_assistant", ctx_vip)
        assert res_vip.enabled is True
        assert res_vip.value is True
        assert res_vip.reason == EvaluationReason.TARGETING_MATCH
        assert res_vip.matched_rule_index == 0

        # Regular user falls back
        ctx_regular = EvaluationContext(entity_id="regular_user_99")
        res_reg = manager.evaluate("ai_assistant", ctx_regular)
        assert res_reg.enabled is False
        assert res_reg.reason == EvaluationReason.DEFAULT_FALLBACK

    def test_attribute_operators(self):
        # Numeric GREATER_THAN rule
        tier_rule = TargetingRule(
            attribute_name="tier_level",
            operator=RuleOperator.GREATER_THAN,
            target_value=3,
            variation=Variation("enterprise_tier", True),
        )
        # String CONTAINS rule
        email_rule = TargetingRule(
            attribute_name="email",
            operator=RuleOperator.CONTAINS,
            target_value="@internal.corp",
            variation=Variation("internal_tester", True),
        )
        # SemVer SEMVER_GTE rule
        version_rule = TargetingRule(
            attribute_name="app_version",
            operator=RuleOperator.SEMVER_GTE,
            target_value="v2.5.0",
            variation=Variation("modern_client", True),
        )

        flag = FeatureFlag(
            key="multi_rule_flag",
            enabled=True,
            targeting_rules=[tier_rule, email_rule, version_rule],
        )

        # Check numeric tier
        assert flag.evaluate(EvaluationContext("u1", {"tier_level": 4})).reason == EvaluationReason.TARGETING_MATCH
        assert flag.evaluate(EvaluationContext("u2", {"tier_level": 2})).reason == EvaluationReason.DEFAULT_FALLBACK

        # Check email domain
        assert flag.evaluate(EvaluationContext("u3", {"email": "alice@internal.corp"})).reason == EvaluationReason.TARGETING_MATCH
        assert flag.evaluate(EvaluationContext("u4", {"email": "bob@gmail.com"})).reason == EvaluationReason.DEFAULT_FALLBACK

        # Check SemVer
        assert flag.evaluate(EvaluationContext("u5", {"app_version": "v2.5.1"})).reason == EvaluationReason.TARGETING_MATCH
        assert flag.evaluate(EvaluationContext("u6", {"app_version": "v2.4.9"})).reason == EvaluationReason.DEFAULT_FALLBACK


class TestPercentageRolloutInvariants:
    def test_deterministic_stickiness(self):
        """Invariant: An entity always evaluates to the exact same bucket regardless of call frequency."""
        flag = FeatureFlag(
            key="checkout_v3",
            enabled=True,
            percentage_rollout=50.0,
            rollout_variation=Variation("on", True),
        )
        ctx = EvaluationContext(entity_id="user_abc_789")

        first_eval = flag.evaluate(ctx)
        for _ in range(50):
            assert flag.evaluate(ctx).value == first_eval.value

    def test_monotonic_inclusion_invariant(self):
        """
        Invariant: If entity X is included at 15% rollout, entity X MUST remain included
        when rollout increases to 30%, 50%, or 100%. No flipping/churn.
        """
        manager = FeatureFlagManager()
        flag = FeatureFlag(
            key="progressive_feature",
            enabled=True,
            percentage_rollout=15.0,
            rollout_variation=Variation("enabled", True),
        )
        manager.register_flag(flag)

        # Find 10 users that are enabled at 15%
        enabled_users = []
        for i in range(200):
            uid = f"user_{i}"
            if manager.is_enabled("progressive_feature", EvaluationContext(uid)):
                enabled_users.append(uid)
                if len(enabled_users) >= 10:
                    break

        assert len(enabled_users) == 10

        # Ramp rollout up to 30% and verify ALL 10 users are STILL enabled
        manager.set_percentage_rollout("progressive_feature", 30.0)
        for uid in enabled_users:
            assert manager.is_enabled("progressive_feature", EvaluationContext(uid)) is True

        # Ramp rollout up to 75% and verify again
        manager.set_percentage_rollout("progressive_feature", 75.0)
        for uid in enabled_users:
            assert manager.is_enabled("progressive_feature", EvaluationContext(uid)) is True

    def test_uniform_hash_distribution(self):
        """
        Verify Murmur/SHA-256 hash buckets allocate uniformly over a sample of 5,000 users.
        A 30% rollout should yield ~30% (+- 2.5%).
        """
        flag = FeatureFlag(
            key="uniformity_test_flag",
            enabled=True,
            percentage_rollout=30.0,
            rollout_variation=Variation("on", True),
        )

        n_samples = 5000
        enabled_count = sum(
            1 for i in range(n_samples)
            if flag.evaluate(EvaluationContext(f"test_entity_{i}")).enabled
        )

        actual_pct = (enabled_count / n_samples) * 100.0
        assert 27.5 <= actual_pct <= 32.5, f"Expected ~30% +-2.5%, got {actual_pct:.2f}%"


class TestMultivariateDistribution:
    def test_multivariate_distribution_and_stickiness(self):
        flag = FeatureFlag(
            key="button_color_experiment",
            enabled=True,
            multivariate_variants=[
                MultivariateVariant(Variation("blue", "blue"), weight=20.0),
                MultivariateVariant(Variation("green", "green"), weight=30.0),
                MultivariateVariant(Variation("orange", "orange"), weight=50.0),
            ],
        )

        # Stickiness test
        ctx = EvaluationContext("user_exp_1")
        first_variant = flag.evaluate(ctx).variation.name
        for _ in range(20):
            assert flag.evaluate(ctx).variation.name == first_variant

        # Statistical spread across 3000 users
        counts = {"blue": 0, "green": 0, "orange": 0}
        for i in range(3000):
            res = flag.evaluate(EvaluationContext(f"user_sample_{i}"))
            counts[res.variation.name] += 1

        blue_pct = (counts["blue"] / 3000) * 100.0
        green_pct = (counts["green"] / 3000) * 100.0
        orange_pct = (counts["orange"] / 3000) * 100.0

        assert 17.0 <= blue_pct <= 23.0
        assert 27.0 <= green_pct <= 33.0
        assert 46.0 <= orange_pct <= 54.0


class TestEmergencyKillSwitch:
    def test_emergency_kill_switch_overrides_everything(self):
        """
        The critical circuit breaker test: Even if targeting rules and 100% rollouts match,
        tripping the emergency kill switch MUST return False/off_variation immediately.
        """
        manager = FeatureFlagManager()
        flag = FeatureFlag(
            key="payment_gateway_v2",
            enabled=True,
            percentage_rollout=100.0,
            default_variation=Variation("v2", True),
            off_variation=Variation("v1_safe_fallback", False),
            targeting_rules=[
                TargetingRule(
                    attribute_name="is_beta",
                    operator=RuleOperator.EQUALS,
                    target_value=True,
                    variation=Variation("v2", True),
                )
            ],
        )
        manager.register_flag(flag)

        ctx = EvaluationContext("vip_user", {"is_beta": True})
        # Before kill switch: Enabled via targeting rule
        assert manager.is_enabled("payment_gateway_v2", ctx) is True

        # TRIP KILL SWITCH
        success = manager.emergency_kill_switch(
            flag_key="payment_gateway_v2",
            actor="oncall_sre",
            reason="High 500 error spike detected in Stripe webhook processing",
        )
        assert success is True

        # Immediately after kill switch: Disabled
        eval_result = manager.evaluate("payment_gateway_v2", ctx)
        assert eval_result.enabled is False
        assert eval_result.value is False
        assert eval_result.reason == EvaluationReason.KILL_SWITCHED
        assert eval_result.variation.name == "v1_safe_fallback"

        # REVIVE FLAG
        revive_success = manager.revive_flag(
            flag_key="payment_gateway_v2",
            actor="oncall_sre",
            reason="Hotfix deployed and verified in staging",
        )
        assert revive_success is True
        assert manager.is_enabled("payment_gateway_v2", ctx) is True


class TestConcurrencyAndAuditTrail:
    def test_concurrent_evaluation_during_kill_switch(self):
        manager = FeatureFlagManager()
        flag = FeatureFlag(
            key="high_load_flag",
            enabled=True,
            percentage_rollout=100.0,
            default_variation=Variation("active", True),
            off_variation=Variation("dormant", False),
        )
        manager.register_flag(flag)

        stop_event = threading.Event()
        evaluations_recorded = []

        def worker(worker_id: int):
            count = 0
            while not stop_event.is_set():
                ctx = EvaluationContext(f"worker_user_{worker_id}_{count}")
                res = manager.evaluate("high_load_flag", ctx)
                evaluations_recorded.append(res.reason)
                count += 1
                time.sleep(0.001)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()

        # Let workers evaluate for 50ms while active
        time.sleep(0.05)

        # Admin trips kill switch mid-flight
        manager.emergency_kill_switch("high_load_flag", "admin", "Load threshold exceeded")

        # Let workers evaluate for another 50ms while kill switched
        time.sleep(0.05)
        stop_event.set()

        for t in threads:
            t.join()

        # Verify we observed evaluations before AND after kill switch
        assert EvaluationReason.PERCENTAGE_ROLLOUT in evaluations_recorded
        assert EvaluationReason.KILL_SWITCHED in evaluations_recorded

    def test_audit_log_completeness(self):
        manager = FeatureFlagManager()
        flag = FeatureFlag(key="audit_flag", enabled=True)
        manager.register_flag(flag, actor="developer_alice")

        manager.set_percentage_rollout("audit_flag", 25.0, actor="product_bob")
        manager.emergency_kill_switch("audit_flag", actor="sre_carol", reason="Latency anomaly")
        manager.revive_flag("audit_flag", actor="sre_carol", reason="False alarm")

        logs = manager.get_audit_log("audit_flag")
        actions = [log.action for log in logs]
        actors = [log.actor for log in logs]

        assert actions == ["REGISTER_FLAG", "UPDATE_ROLLOUT", "EMERGENCY_KILL_SWITCH", "REVIVE_FLAG"]
        assert actors == ["developer_alice", "product_bob", "sre_carol", "sre_carol"]
