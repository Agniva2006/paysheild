from typing import Dict, Any, Tuple, Optional

class RuleGuardrails:
    """
    Pre-ML Hybrid Defense-in-Depth Rules Engine.
    Executes instant zero-latency checks before passing to ML inference.
    """
    _instance: Optional["RuleGuardrails"] = None

    def __init__(self):
        pass

    @classmethod
    def get_instance(cls) -> "RuleGuardrails":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def evaluate(self, raw_data: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """No local hard blocks: IEEE-CIS card/address IDs are not live identifiers.

        Hard blocking requires trusted provider signals and merchant-specific limits;
        this service has neither, so dataset IDs and arbitrary amount cutoffs must
        not be presented as a stolen-card list, sanctions feed, or replay detector.
        """
        return False, None, None

def get_guardrails() -> RuleGuardrails:
    return RuleGuardrails.get_instance()

