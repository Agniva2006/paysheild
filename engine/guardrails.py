import time
from typing import Dict, Any, Tuple, Optional
from collections import defaultdict, deque

# Stolen or compromised card test blacklist
STOLEN_CARD_BLACKLIST = {
    9999,
    1111,
    404040,
    950000,
}

# Sanctioned or prohibited region codes
SANCTIONED_REGIONS = {999, 9999}

# Maximum single transaction hard limit
HARD_MAX_TRANSACTION_AMOUNT = 50000.0  # $50,000 ceiling

class RuleGuardrails:
    """
    Pre-ML Hybrid Defense-in-Depth Rules Engine.
    Executes instant zero-latency checks before passing to ML inference.
    """
    _instance: Optional["RuleGuardrails"] = None

    def __init__(self):
        # In-memory card velocity tracker: card1 -> deque of timestamps
        self.card_velocity_tracker = defaultdict(lambda: deque(maxlen=20))

    @classmethod
    def get_instance(cls) -> "RuleGuardrails":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def evaluate(self, raw_data: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Evaluates deterministic hard rules.
        Returns: (triggered, rule_name, reason)
        """
        amt = float(raw_data.get("TransactionAmt", 0.0) or 0.0)
        card1 = raw_data.get("card1")
        addr2 = raw_data.get("addr2")
        now = time.time()

        # Rule 1: Hard Maximum Amount Limit
        if amt >= HARD_MAX_TRANSACTION_AMOUNT:
            return (
                True,
                "RULE_AMOUNT_CEILING_BREACH",
                f"Transaction amount (${amt:,.2f}) exceeds platform maximum threshold (${HARD_MAX_TRANSACTION_AMOUNT:,.2f}). Immediate security block."
            )

        # Rule 2: Stolen / Compromised Card Blacklist
        if card1 is not None:
            try:
                card1_int = int(card1)
                if card1_int in STOLEN_CARD_BLACKLIST:
                    return (
                        True,
                        "RULE_BLACKLISTED_CARD_BIN",
                        f"Card identifier ({card1_int}) is present on global fraud/stolen card blacklist."
                    )
            except (ValueError, TypeError):
                pass

        # Rule 3: Sanctioned Geographic Region
        if addr2 is not None:
            try:
                addr2_int = int(addr2)
                if addr2_int in SANCTIONED_REGIONS:
                    return (
                        True,
                        "RULE_SANCTIONED_REGION_RESTRICTION",
                        f"Transaction destination (addr2: {addr2_int}) is in a prohibited or sanctioned regulatory jurisdiction."
                    )
            except (ValueError, TypeError):
                pass

        # Rule 4: Card Replay Velocity Flood Check (e.g., > 5 requests in 30 seconds for same card)
        if card1 is not None:
            timestamps = self.card_velocity_tracker[card1]
            # Prune timestamps older than 30 seconds
            while timestamps and (now - timestamps[0]) > 30.0:
                timestamps.popleft()
            timestamps.append(now)

            if len(timestamps) >= 6:
                return (
                    True,
                    "RULE_HIGH_FREQUENCY_REPLAY_ATTACK",
                    f"Card replay flood detected ({len(timestamps)} attempts within 30 seconds). Velocity lock engaged."
                )

        return False, None, None

def get_guardrails() -> RuleGuardrails:
    return RuleGuardrails.get_instance()

