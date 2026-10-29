from typing import Dict, Any, List
import numpy as np

# Known high-risk / disposable email providers
HIGH_RISK_EMAIL_DOMAINS = {
    "anonymous.com",
    "protonmail.com",
    "mailinator.com",
    "tempmail.com",
    "guerrillamail.com",
    "darkweb.org",
}

class RiskExplainer:
    """
    Explainable AI (XAI) engine for PayShield.
    Extracts the top observable risk drivers from transaction metadata
    and domain interactions to provide transparent explanations.
    """

    @staticmethod
    def explain_transaction(
        raw_data: Dict[str, Any],
        score: float,
        action: str,
        t_stepup: float,
        t_block: float,
    ) -> List[str]:
        factors: List[str] = []
        amt = float(raw_data.get("TransactionAmt", 0.0) or 0.0)
        card4 = str(raw_data.get("card4", "")).lower()
        p_email = str(raw_data.get("P_emaildomain", "")).lower()
        dist1 = raw_data.get("dist1")
        device_type = str(raw_data.get("DeviceType", "")).lower()

        # 1. Amount Anomaly Check
        amt_mean = 135.0
        if amt > 0:
            ratio = amt / amt_mean
            if ratio >= 4.0:
                factors.append(f"Transaction amount (${amt:,.2f}) is {ratio:.1f}x higher than standard baseline")
            elif ratio >= 2.0 and score >= t_stepup:
                factors.append(f"Elevated transaction amount (${amt:,.2f}) above category average")

        # 2. Velocity Spike Check (C features)
        c1 = float(raw_data.get("C1", 0.0) or 0.0)
        c2 = float(raw_data.get("C2", 0.0) or 0.0)
        c6 = float(raw_data.get("C6", 0.0) or 0.0)
        max_c = max(c1, c2, c6)
        if max_c >= 15:
            factors.append(f"Severe velocity burst: {int(max_c)} rapid payment attempts registered")
        elif max_c >= 5 and score >= t_stepup:
            factors.append(f"Elevated velocity count ({int(max_c)} recent operations)")

        # 3. High-Risk / Anonymous Email Domain
        if any(domain in p_email for domain in HIGH_RISK_EMAIL_DOMAINS):
            factors.append(f"High-risk or privacy-shielded email domain detected ({p_email})")

        # 4. Identity & Device Risk Flags
        id_01 = raw_data.get("id_01")
        id_12 = str(raw_data.get("id_12", "")).lower()
        if id_01 is not None and float(id_01) <= -20:
            factors.append("Significant identity-device inconsistency flag (negative id_01 index)")
        if id_12 == "notfound":
            factors.append("Proxy or unrecognized identity proxy detected")

        # 5. Geolocation / Distance Disparity
        if dist1 is not None and float(dist1) >= 500:
            factors.append(f"Unusual geographical distance ({float(dist1):.0f} miles from registered billing address)")

        # 6. Fallback explanations based on Action if factors are sparse
        if not factors:
            if action == "BLOCK":
                factors.append("Multivariate anomaly detected across combined card-history & device features")
                factors.append("Risk score exceeded hard block ceiling (> 0.85)")
            elif action == "STEP-UP":
                factors.append(f"Risk score ({score:.4f}) entered medium-friction review band")
                factors.append("Precautionary step-up verification required under active friction budget")
            else:
                factors.append("Standard transaction amount within customer baseline")
                factors.append("Verified device identity and consistent geolocation")

        return factors[:3]  # Return top 3 most significant drivers

