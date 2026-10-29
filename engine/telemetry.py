import time
import datetime
import asyncio
from collections import deque
from typing import Dict, Any, List, Optional
import numpy as np

from config import MAX_RECENT_DECISIONS_BUFFER

class TelemetryMonitor:
    _instance: Optional["TelemetryMonitor"] = None

    def __init__(self, max_buffer_size: int = MAX_RECENT_DECISIONS_BUFFER):
        self.max_buffer_size = max_buffer_size
        self.total_transactions: int = 0
        self.allow_count: int = 0
        self.stepup_count: int = 0
        self.block_count: int = 0
        self.guardrail_block_count: int = 0

        # Step-Up Verification lifecycle counts
        self.stepup_verified_count: int = 0
        self.stepup_failed_count: int = 0
        self.pending_challenges: Dict[str, Dict[str, Any]] = {}

        self.total_amount_usd: float = 0.0
        self.allowed_amount_usd: float = 0.0
        self.stepup_amount_usd: float = 0.0
        self.blocked_amount_usd: float = 0.0

        self.latencies_ms: deque = deque(maxlen=max_buffer_size)
        self.recent_events: deque = deque(maxlen=max_buffer_size)
        self.policy_change_history: List[Dict[str, Any]] = []

        # Real-time SSE / WebSocket subscriber queues
        self.subscribers: List[asyncio.Queue] = []

    @classmethod
    def get_instance(cls) -> "TelemetryMonitor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(self) -> asyncio.Queue:
        """Registers a new real-time event listener queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Removes a listener queue."""
        if q in self.subscribers:
            self.subscribers.remove(q)

    def _broadcast_event(self, event: Dict[str, Any]) -> None:
        """Pushes event to all active real-time subscribers."""
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
            except Exception:
                pass

    def record_decision(
        self,
        transaction_id: str,
        amount: float,
        score: float,
        action: str,
        thresholds: Dict[str, float],
        friction_budget: float,
        latency_ms: float,
        risk_factors: Optional[List[str]] = None,
        guardrail_triggered: bool = False,
    ) -> Dict[str, Any]:
        """Records telemetry for an evaluated transaction and dispatches to live feed."""
        self.total_transactions += 1
        amt = float(amount or 0.0)
        self.total_amount_usd += amt
        self.latencies_ms.append(latency_ms)

        if guardrail_triggered:
            self.guardrail_block_count += 1

        if action == "ALLOW":
            self.allow_count += 1
            self.allowed_amount_usd += amt
        elif action == "STEP-UP":
            self.stepup_count += 1
            self.stepup_amount_usd += amt
            # Register pending step-up challenge (Demo standard OTP: 123456)
            self.pending_challenges[transaction_id] = {
                "transaction_id": transaction_id,
                "amount": amt,
                "expected_otp": "123456",
                "status": "PENDING",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        elif action == "BLOCK":
            self.block_count += 1
            self.blocked_amount_usd += amt

        event = {
            "type": "NEW_TRANSACTION",
            "transaction_id": transaction_id,
            "amount": round(amt, 2),
            "fraud_risk_score": round(score, 6),
            "action": action,
            "friction_budget": friction_budget,
            "t_stepup": round(thresholds.get("t_stepup", 0.0), 6),
            "t_block": round(thresholds.get("t_block", 0.0), 4),
            "latency_ms": round(latency_ms, 2),
            "risk_factors": risk_factors or [],
            "guardrail_triggered": guardrail_triggered,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.recent_events.append(event)
        self._broadcast_event(event)
        return event

    def verify_stepup(self, transaction_id: str, otp_code: str) -> Dict[str, Any]:
        """
        Validates the secondary authentication OTP challenge for a stepped-up transaction.
        Closes the loop between user verification and risk engine.
        """
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Check if transaction has pending challenge
        challenge = self.pending_challenges.get(transaction_id)
        if not challenge:
            # Fallback for dynamic demo: allow verifying any valid-formatted ID
            expected_otp = "123456"
            amt = 0.0
        else:
            expected_otp = challenge.get("expected_otp", "123456")
            amt = challenge.get("amount", 0.0)

        if str(otp_code).strip() == expected_otp:
            self.stepup_verified_count += 1
            if challenge:
                challenge["status"] = "VERIFIED"
                challenge["verified_at"] = now_ts
                self.pending_challenges.pop(transaction_id, None)

            event = {
                "type": "STEP_UP_VERIFIED",
                "transaction_id": transaction_id,
                "amount": amt,
                "action": "VERIFIED_ALLOW",
                "status": "SUCCESS",
                "message": "OTP verification successful. Transaction released and authorized.",
                "timestamp": now_ts,
            }
            self.recent_events.append(event)
            self._broadcast_event(event)
            return event
        else:
            self.stepup_failed_count += 1
            if challenge:
                challenge["status"] = "FAILED"

            event = {
                "type": "STEP_UP_FAILED",
                "transaction_id": transaction_id,
                "amount": amt,
                "action": "CHALLENGE_FAILED",
                "status": "FAILED",
                "message": "Invalid OTP code provided. Transaction blocked for customer security.",
                "timestamp": now_ts,
            }
            self.recent_events.append(event)
            self._broadcast_event(event)
            return event

    def record_policy_change(self, change_record: Dict[str, Any]) -> None:
        """Logs a policy threshold recalibration event and broadcasts update."""
        self.policy_change_history.append(change_record)
        event = {
            "type": "POLICY_RECALIBRATED",
            "details": change_record,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._broadcast_event(event)

    def get_metrics(self, active_budget: float) -> Dict[str, Any]:
        """Calculates live operational metrics and closed-loop feedback statistics."""
        total = self.total_transactions
        if total == 0:
            return {
                "total_transactions": 0,
                "action_distribution": {
                    "allow": {"count": 0, "rate": 0.0},
                    "step_up": {"count": 0, "rate": 0.0},
                    "block": {"count": 0, "rate": 0.0},
                },
                "step_up_resolution": {
                    "pending": 0,
                    "verified": 0,
                    "failed": 0,
                    "resolution_rate": 0.0,
                },
                "guardrail_blocks": 0,
                "financial_impact": {
                    "total_amount_processed": 0.0,
                    "allowed_amount": 0.0,
                    "stepup_amount": 0.0,
                    "fraud_dollars_prevented": 0.0,
                },
                "friction_compliance": {
                    "configured_budget": active_budget,
                    "observed_stepup_rate": 0.0,
                    "budget_violation": False,
                    "status": "HEALTHY",
                },
                "latency_stats_ms": {"mean": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0},
                "recent_events_count": 0,
            }

        stepup_rate = self.stepup_count / total
        allow_rate = self.allow_count / total
        block_rate = self.block_count / total

        # Budget compliance check
        is_violated = stepup_rate > (active_budget * 1.05)
        status = "BUDGET_EXCEEDED" if is_violated else "COMPLIANT"

        lat_arr = np.array(self.latencies_ms) if self.latencies_ms else np.array([0.0])

        stepup_resolved = self.stepup_verified_count + self.stepup_failed_count
        res_rate = round(stepup_resolved / self.stepup_count, 4) if self.stepup_count > 0 else 1.0

        return {
            "total_transactions": total,
            "action_distribution": {
                "allow": {"count": self.allow_count, "rate": round(allow_rate, 4)},
                "step_up": {"count": self.stepup_count, "rate": round(stepup_rate, 4)},
                "block": {"count": self.block_count, "rate": round(block_rate, 4)},
            },
            "step_up_resolution": {
                "pending": len(self.pending_challenges),
                "verified": self.stepup_verified_count,
                "failed": self.stepup_failed_count,
                "resolution_rate": res_rate,
            },
            "guardrail_blocks": self.guardrail_block_count,
            "financial_impact": {
                "total_amount_processed": round(self.total_amount_usd, 2),
                "allowed_amount": round(self.allowed_amount_usd, 2),
                "stepup_amount": round(self.stepup_amount_usd, 2),
                "fraud_dollars_prevented": round(self.blocked_amount_usd, 2),
            },
            "friction_compliance": {
                "configured_budget": active_budget,
                "observed_stepup_rate": round(stepup_rate, 4),
                "budget_violation": is_violated,
                "status": status,
            },
            "latency_stats_ms": {
                "mean": round(float(np.mean(lat_arr)), 2),
                "p95": round(float(np.percentile(lat_arr, 95)), 2),
                "min": round(float(np.min(lat_arr)), 2),
                "max": round(float(np.max(lat_arr)), 2),
            },
            "recent_events_count": len(self.recent_events),
        }

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the most recent transaction decisions."""
        events = list(self.recent_events)
        return events[-limit:]

    def reset(self) -> None:
        """Resets all telemetry counters."""
        self.total_transactions = 0
        self.allow_count = 0
        self.stepup_count = 0
        self.block_count = 0
        self.guardrail_block_count = 0
        self.stepup_verified_count = 0
        self.stepup_failed_count = 0
        self.pending_challenges.clear()
        self.total_amount_usd = 0.0
        self.allowed_amount_usd = 0.0
        self.stepup_amount_usd = 0.0
        self.blocked_amount_usd = 0.0
        self.latencies_ms.clear()
        self.recent_events.clear()
        self.policy_change_history.clear()

def get_telemetry_monitor() -> TelemetryMonitor:
    return TelemetryMonitor.get_instance()
