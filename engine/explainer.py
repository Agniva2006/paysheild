from typing import List

class RiskExplainer:
    """Build user-facing explanations from model output."""

    @staticmethod
    def explain_model_contributions(contributions: List[tuple], limit: int = 3) -> List[str]:
        """Describe actual LightGBM contributions; positive values raise raw model margin."""
        positive = [(name, float(value)) for name, value in contributions if float(value) > 1e-8]
        positive.sort(key=lambda item: item[1], reverse=True)
        if not positive:
            return ["No positive feature contributions were available from the model artifact."]
        labels = {
            "TransactionAmt": "Transaction amount",
            "ProductCD": "Transaction category",
            "card4": "Card network",
            "card6": "Card type",
            "DeviceType": "Device type",
            "DeviceInfo": "Device/browser information",
        }
        return [
            f"{labels.get(name, name)} increased the model margin (contribution {value:+.3f} log-odds)."
            for name, value in positive[:limit]
        ]

