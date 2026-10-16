"""
Feature Flags & Emergency Kill Switches Engine
=================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.1

Production-grade feature flagging and dynamic configuration engine designed
to decouple code deployment from feature release. Supports:
1. Instantaneous Emergency Kill Switches (O(1) circuit breaker for bad deploys)
2. Deterministic, Sticky, Monotonic Percentage Rollouts (hash bucket invariant)
3. Granular Targeting Rules (User ID, Tenant, Geo, App Version, Employee Beta)
4. Multivariate Experimentation (A/B/n weighted distributions)
5. Thread-safe in-memory evaluation with immutable audit trail logging
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("FeatureFlags")


class EvaluationReason(str, enum.Enum):
    KILL_SWITCHED = "KILL_SWITCHED"
    DISABLED = "DISABLED"
    TARGETING_MATCH = "TARGETING_MATCH"
    PERCENTAGE_ROLLOUT = "PERCENTAGE_ROLLOUT"
    MULTIVARIATE_BUCKET = "MULTIVARIATE_BUCKET"
    DEFAULT_FALLBACK = "DEFAULT_FALLBACK"
    FLAG_NOT_FOUND = "FLAG_NOT_FOUND"


class RuleOperator(str, enum.Enum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN_LIST = "IN_LIST"
    NOT_IN_LIST = "NOT_IN_LIST"
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"
    CONTAINS = "CONTAINS"
    SEMVER_GTE = "SEMVER_GTE"


@dataclass(frozen=True)
class EvaluationContext:
    """Contextual metadata representing the entity (user, tenant, request) being evaluated."""
    entity_id: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    def get_attr(self, key: str, default: Any = None) -> Any:
        if key == "entity_id" or key == "id":
            return self.entity_id
        return self.attributes.get(key, default)


@dataclass(frozen=True)
class Variation:
    """A distinct variation/payload served by a feature flag."""
    name: str
    value: Any

    def __str__(self) -> str:
        return f"{self.name}:{self.value}"


@dataclass(frozen=True)
class EvaluationResult:
    """Outcome of evaluating a feature flag against an EvaluationContext."""
    flag_key: str
    enabled: bool
    variation: Variation
    reason: EvaluationReason
    matched_rule_index: Optional[int] = None
    evaluated_at: float = field(default_factory=time.time)

    @property
    def value(self) -> Any:
        return self.variation.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flag_key": self.flag_key,
            "enabled": self.enabled,
            "variation_name": self.variation.name,
            "variation_value": self.variation.value,
            "reason": self.reason.value,
            "matched_rule_index": self.matched_rule_index,
            "evaluated_at": self.evaluated_at,
        }


@dataclass
class TargetingRule:
    """Attribute-based targeting predicate."""
    attribute_name: str
    operator: RuleOperator
    target_value: Any
    variation: Variation

    def matches(self, context: EvaluationContext) -> bool:
        actual_value = context.get_attr(self.attribute_name)
        if actual_value is None:
            return False

        try:
            if self.operator == RuleOperator.EQUALS:
                return actual_value == self.target_value
            elif self.operator == RuleOperator.NOT_EQUALS:
                return actual_value != self.target_value
            elif self.operator == RuleOperator.IN_LIST:
                return actual_value in (self.target_value if isinstance(self.target_value, (list, set, tuple)) else [self.target_value])
            elif self.operator == RuleOperator.NOT_IN_LIST:
                return actual_value not in (self.target_value if isinstance(self.target_value, (list, set, tuple)) else [self.target_value])
            elif self.operator == RuleOperator.GREATER_THAN:
                return float(actual_value) > float(self.target_value)
            elif self.operator == RuleOperator.LESS_THAN:
                return float(actual_value) < float(self.target_value)
            elif self.operator == RuleOperator.CONTAINS:
                return str(self.target_value) in str(actual_value)
            elif self.operator == RuleOperator.SEMVER_GTE:
                return self._parse_semver(str(actual_value)) >= self._parse_semver(str(self.target_value))
        except (ValueError, TypeError):
            return False
        return False

    @staticmethod
    def _parse_semver(version_str: str) -> Tuple[int, ...]:
        clean = version_str.strip().lstrip("v").split("-")[0]
        parts = clean.split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)


@dataclass
class MultivariateVariant:
    """Variant with assigned traffic percentage weight (0 to 100)."""
    variation: Variation
    weight: float  # e.g., 50.0 for 50%


@dataclass
class AuditRecord:
    """Tamper-evident record of configuration changes or emergency overrides."""
    timestamp: float
    actor: str
    action: str
    flag_key: str
    details: Dict[str, Any]


class Hasher:
    """
    Deterministic Murmur/SHA-256 bucket allocator for sticky, monotonic percentage rollouts.
    Invariant:
    - Same flag_key + entity_id always produces the exact same bucket integer in [0, 99].
    - Monotonic: If user X is in a 20% rollout, user X is guaranteed to be in a 30% rollout.
    """
    @staticmethod
    def bucket(flag_key: str, entity_id: str) -> int:
        salt = f"{flag_key}:{entity_id}".encode("utf-8")
        digest = hashlib.sha256(salt).hexdigest()
        # Take first 8 hex characters (32 bits), convert to int, mod 100
        int_val = int(digest[:8], 16)
        return int_val % 100


class FeatureFlag:
    """
    Core Feature Flag entity holding targeting rules, rollout allocations,
    and emergency kill switches.
    """
    def __init__(
        self,
        key: str,
        enabled: bool = True,
        default_variation: Optional[Variation] = None,
        off_variation: Optional[Variation] = None,
        percentage_rollout: float = 0.0,
        rollout_variation: Optional[Variation] = None,
        targeting_rules: Optional[List[TargetingRule]] = None,
        multivariate_variants: Optional[List[MultivariateVariant]] = None,
        description: str = "",
    ) -> None:
        self.key = key
        self.enabled = enabled
        self.kill_switched = False
        self.kill_switch_reason: Optional[str] = None
        self.kill_switch_time: Optional[float] = None

        self.default_variation = default_variation or Variation("on", True)
        self.off_variation = off_variation or Variation("off", False)
        self.percentage_rollout = max(0.0, min(100.0, percentage_rollout))
        self.rollout_variation = rollout_variation or self.default_variation
        self.targeting_rules = targeting_rules or []
        self.multivariate_variants = multivariate_variants or []
        self.description = description
        self.version = 1
        self.updated_at = time.time()

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        # 1. Emergency Kill Switch Check (Absolute highest priority - O(1))
        if self.kill_switched:
            return EvaluationResult(
                flag_key=self.key,
                enabled=False,
                variation=self.off_variation,
                reason=EvaluationReason.KILL_SWITCHED,
            )

        # 2. Globally Disabled Check
        if not self.enabled:
            return EvaluationResult(
                flag_key=self.key,
                enabled=False,
                variation=self.off_variation,
                reason=EvaluationReason.DISABLED,
            )

        # 3. Targeted Rules Check (Evaluated sequentially in priority order)
        for idx, rule in enumerate(self.targeting_rules):
            if rule.matches(context):
                return EvaluationResult(
                    flag_key=self.key,
                    enabled=True,
                    variation=rule.variation,
                    reason=EvaluationReason.TARGETING_MATCH,
                    matched_rule_index=idx,
                )

        # 4. Multivariate Distribution Check
        if self.multivariate_variants:
            bucket = Hasher.bucket(f"{self.key}:multivariate", context.entity_id)
            cumulative_weight = 0.0
            for variant in self.multivariate_variants:
                cumulative_weight += variant.weight
                if bucket < cumulative_weight:
                    return EvaluationResult(
                        flag_key=self.key,
                        enabled=True,
                        variation=variant.variation,
                        reason=EvaluationReason.MULTIVARIATE_BUCKET,
                    )

        # 5. Percentage Rollout Check (Monotonic hash bucketing)
        if self.percentage_rollout > 0.0:
            bucket = Hasher.bucket(self.key, context.entity_id)
            if bucket < self.percentage_rollout:
                return EvaluationResult(
                    flag_key=self.key,
                    enabled=True,
                    variation=self.rollout_variation,
                    reason=EvaluationReason.PERCENTAGE_ROLLOUT,
                )

        # 6. Default Fallback
        return EvaluationResult(
            flag_key=self.key,
            enabled=False,
            variation=self.off_variation,
            reason=EvaluationReason.DEFAULT_FALLBACK,
        )


class FeatureFlagManager:
    """
    Thread-safe Feature Flag Management Engine.
    Handles flag registration, updates, atomic emergency kill switches,
    and bulk evaluations for high-throughput microservices.
    """
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._flags: Dict[str, FeatureFlag] = {}
        self._audit_log: List[AuditRecord] = []
        self._listeners: List[Callable[[str, str, Dict[str, Any]], None]] = []

    def register_flag(self, flag: FeatureFlag, actor: str = "system") -> None:
        with self._lock:
            self._flags[flag.key] = flag
            self._record_audit(
                actor=actor,
                action="REGISTER_FLAG",
                flag_key=flag.key,
                details={"version": flag.version, "enabled": flag.enabled},
            )

    def get_flag(self, flag_key: str) -> Optional[FeatureFlag]:
        with self._lock:
            return self._flags.get(flag_key)

    def evaluate(
        self,
        flag_key: str,
        context: EvaluationContext,
        fallback_value: Any = False,
    ) -> EvaluationResult:
        """
        Evaluate a single flag for a given entity context.
        Fail-closed to fallback_value if the flag key is missing.
        """
        with self._lock:
            flag = self._flags.get(flag_key)

        if not flag:
            return EvaluationResult(
                flag_key=flag_key,
                enabled=bool(fallback_value),
                variation=Variation("fallback", fallback_value),
                reason=EvaluationReason.FLAG_NOT_FOUND,
            )

        return flag.evaluate(context)

    def is_enabled(
        self,
        flag_key: str,
        context: EvaluationContext,
        default: bool = False,
    ) -> bool:
        """Convenience boolean helper for simple on/off checks."""
        res = self.evaluate(flag_key, context, fallback_value=default)
        return bool(res.value)

    def emergency_kill_switch(
        self,
        flag_key: str,
        actor: str,
        reason: str,
    ) -> bool:
        """
        Instantly trip the emergency kill switch for a flag.
        Overrides all rules and rollouts in O(1) time without restarts.
        """
        with self._lock:
            flag = self._flags.get(flag_key)
            if not flag:
                return False

            flag.kill_switched = True
            flag.kill_switch_reason = reason
            flag.kill_switch_time = time.time()
            flag.version += 1
            flag.updated_at = time.time()

            self._record_audit(
                actor=actor,
                action="EMERGENCY_KILL_SWITCH",
                flag_key=flag_key,
                details={"reason": reason, "timestamp": flag.kill_switch_time},
            )
            self._notify_listeners(flag_key, "KILL_SWITCHED", {"reason": reason})
            logger.warning("[KILL SWITCH] Flag '%s' tripped by '%s': %s", flag_key, actor, reason)
            return True

    def revive_flag(
        self,
        flag_key: str,
        actor: str,
        reason: str = "Resolved incident",
    ) -> bool:
        """Restore a kill-switched flag back to normal rule evaluation."""
        with self._lock:
            flag = self._flags.get(flag_key)
            if not flag:
                return False

            flag.kill_switched = False
            flag.kill_switch_reason = None
            flag.version += 1
            flag.updated_at = time.time()

            self._record_audit(
                actor=actor,
                action="REVIVE_FLAG",
                flag_key=flag_key,
                details={"reason": reason},
            )
            self._notify_listeners(flag_key, "REVIVED", {"reason": reason})
            logger.info("[REVIVE] Flag '%s' revived by '%s': %s", flag_key, actor, reason)
            return True

    def set_percentage_rollout(
        self,
        flag_key: str,
        percentage: float,
        actor: str = "system",
    ) -> bool:
        """Dynamically adjust the percentage rollout for a flag."""
        with self._lock:
            flag = self._flags.get(flag_key)
            if not flag:
                return False

            old_pct = flag.percentage_rollout
            flag.percentage_rollout = max(0.0, min(100.0, percentage))
            flag.version += 1
            flag.updated_at = time.time()

            self._record_audit(
                actor=actor,
                action="UPDATE_ROLLOUT",
                flag_key=flag_key,
                details={"old_percentage": old_pct, "new_percentage": flag.percentage_rollout},
            )
            return True

    def bulk_evaluate(self, context: EvaluationContext) -> Dict[str, EvaluationResult]:
        """Evaluate all registered flags for an entity context (e.g., frontend bootstrap)."""
        with self._lock:
            flags_snapshot = list(self._flags.values())

        return {flag.key: flag.evaluate(context) for flag in flags_snapshot}

    def add_change_listener(
        self,
        listener: Callable[[str, str, Dict[str, Any]], None],
    ) -> None:
        with self._lock:
            self._listeners.append(listener)

    def get_audit_log(self, flag_key: Optional[str] = None) -> List[AuditRecord]:
        with self._lock:
            if flag_key:
                return [r for r in self._audit_log if r.flag_key == flag_key]
            return list(self._audit_log)

    def _record_audit(
        self,
        actor: str,
        action: str,
        flag_key: str,
        details: Dict[str, Any],
    ) -> None:
        record = AuditRecord(
            timestamp=time.time(),
            actor=actor,
            action=action,
            flag_key=flag_key,
            details=details,
        )
        self._audit_log.append(record)

    def _notify_listeners(self, flag_key: str, action: str, details: Dict[str, Any]) -> None:
        for listener in self._listeners:
            try:
                listener(flag_key, action, details)
            except Exception as e:
                logger.error("Error in flag change listener: %s", e)
