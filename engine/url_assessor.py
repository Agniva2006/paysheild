"""Assess payment URLs from URL features, DNS, TLS and optional threat intelligence."""

import ipaddress
import json
import math
import os
import socket
import ssl
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "is.gd", "ow.ly", "rb.gy", "cutt.ly"}
REDIRECT_PARAMETERS = {"url", "u", "redirect", "redirect_url", "redirect_uri", "return", "return_to", "next", "target", "dest", "destination", "continue", "callback"}


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {character: value.count(character) for character in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _resolve_public_host(host: str) -> list[tuple[int, tuple]]:
    """Resolve the host and reject private, local, or reserved destinations."""
    resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    endpoints = []
    for family, socktype, proto, _, sockaddr in resolved:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("This address resolves to a private or reserved network and cannot be checked.")
        endpoints.append((family, socktype, proto, sockaddr))
    if not endpoints:
        raise OSError("No network addresses found for this domain.")
    return endpoints


def _check_tls(host: str, endpoints: list[tuple[int, int, int, tuple]]) -> dict:
    context = ssl.create_default_context()
    certificate_errors = []
    network_errors = []
    for family, socktype, proto, sockaddr in endpoints:
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(3)
        try:
            raw.connect(sockaddr)
            with context.wrap_socket(raw, server_hostname=host) as secure:
                cert = secure.getpeercert()
                return {
                    "valid": True,
                    "tls_version": secure.version(),
                    "certificate_expires": cert.get("notAfter"),
                    "status": "valid",
                }
        except ssl.SSLCertVerificationError:
            certificate_errors.append("certificate_verification_failed")
            raw.close()
        except ssl.SSLError:
            certificate_errors.append("tls_handshake_failed")
            raw.close()
        except (OSError, ssl.SSLError) as exc:
            network_errors.append(type(exc).__name__)
            raw.close()
    if certificate_errors:
        return {"valid": False, "status": "invalid", "errors": sorted(set(certificate_errors))}
    return {"valid": None, "status": "unavailable", "errors": sorted(set(network_errors))}


def _check_web_risk(url: str) -> dict:
    api_key = os.getenv("GOOGLE_WEB_RISK_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "threats": []}
    query = urlencode([
        ("threatTypes", "MALWARE"),
        ("threatTypes", "SOCIAL_ENGINEERING"),
        ("threatTypes", "UNWANTED_SOFTWARE"),
        ("uri", url),
        ("key", api_key),
    ])
    request = Request(
        "https://webrisk.googleapis.com/v1/uris:search?" + query,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=4) as response:
            result = json.loads(response.read().decode("utf-8"))
        threats = result.get("threat", {}).get("threatTypes", [])
        return {
            "status": "threat_match" if threats else "no_match",
            "threats": sorted(set(threats)),
        }
    except Exception:
        return {"status": "unavailable", "threats": []}


def assess_url(raw_url: str) -> dict:
    value = raw_url.strip()
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only http and https website links can be assessed.")
    if parsed.username or parsed.password:
        raise ValueError("Links containing embedded usernames or passwords are not accepted.")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or len(host) > 253 or any(ch.isspace() for ch in host):
        raise ValueError("Enter a valid website link.")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("This website address could not be normalized.") from exc
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a website URL with a valid port number.") from exc

    # Checkout paths and query strings can carry order IDs or one-time tokens.
    # Inspect their structure locally, but only retain/share the origin.
    clean_url = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "/", "", ""))
    signals = []
    transport_points = 0
    domain_points = 0
    redirect_points = 0
    url_structure_points = 0
    network_points = 0
    network_evidence: dict = {"dns_status": "not_checked", "tls_status": "not_checked"}

    if parsed.scheme.lower() != "https":
        transport_points = max(transport_points, 25)
        signals.append({"code": "NO_HTTPS", "severity": "elevated", "text": "The link uses HTTP, so the connection is not protected by HTTPS."})
    else:
        signals.append({"code": "HTTPS", "severity": "info", "text": "The link uses HTTPS. This protects the connection but does not prove the merchant is trustworthy."})

    try:
        ipaddress.ip_address(host.strip("[]"))
        domain_points += 40
        signals.append({"code": "IP_HOST", "severity": "high", "text": "The website address uses a raw IP address instead of a domain name."})
    except ValueError:
        pass

    if "xn--" in host:
        domain_points += 25
        signals.append({"code": "PUNYCODE", "severity": "high", "text": "The domain contains an internationalized label that can be used to imitate other names."})

    if host in SHORTENERS:
        redirect_points = 20
        signals.append({"code": "SHORTENER", "severity": "elevated", "text": "The link uses a URL-shortening service, so its final destination is not visible here."})

    labels = host.split(".")
    if len(labels) > 5:
        domain_points += 10
        signals.append({"code": "MANY_SUBDOMAINS", "severity": "elevated", "text": "The address has many subdomains; check the registered domain carefully."})
    if host.count("-") >= 3:
        domain_points += 10
        signals.append({"code": "HYPHENATED_DOMAIN", "severity": "elevated", "text": "The domain contains several hyphens, which can make lookalike addresses harder to spot."})

    host_labels = host.split(".")
    longest_label = max(host_labels, key=len)
    if len(longest_label) >= 40:
        domain_points += 8
        signals.append({"code": "LONG_HOST_LABEL", "severity": "elevated", "text": "One hostname label is unusually long; inspect the domain carefully."})
    if len(longest_label) >= 20 and _entropy(longest_label) >= 3.7:
        domain_points += 8
        signals.append({"code": "HIGH_ENTROPY_HOST_LABEL", "severity": "elevated", "text": "A long hostname label has high character variety, which can occur in generated or obfuscated addresses."})

    path_segments = [part for part in parsed.path.split("/") if part]
    query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
    if len(value) >= 250:
        url_structure_points += 6
        signals.append({"code": "LONG_URL", "severity": "elevated", "text": "The full payment URL is unusually long."})
    if len(path_segments) >= 6:
        url_structure_points += 6
        signals.append({"code": "DEEP_URL_PATH", "severity": "elevated", "text": "The URL has many nested path segments."})
    if len(query_pairs) >= 8:
        url_structure_points += 5
        signals.append({"code": "MANY_QUERY_PARAMETERS", "severity": "elevated", "text": "The URL contains many query parameters, making its destination harder to inspect."})
    redirect_values = [item for key, item in query_pairs if key.lower() in REDIRECT_PARAMETERS and item]
    if any(item.startswith(("http://", "https://", "//")) for item in redirect_values):
        redirect_points += 12
        signals.append({"code": "EXTERNAL_REDIRECT_PARAMETER", "severity": "elevated", "text": "A URL parameter contains another destination, so the link may redirect to a different website."})

    try:
        if port not in (None, 80, 443):
            transport_points = max(transport_points, 10)
            signals.append({"code": "NONSTANDARD_PORT", "severity": "elevated", "text": "The payment URL uses a nonstandard web port."})
        endpoints = _resolve_public_host(host)
        network_evidence["dns_status"] = "resolved_public"
        network_evidence["resolved_address_count"] = len(endpoints)
        signals.append({"code": "DNS_RESOLVED", "severity": "info", "text": "The domain resolved to public network addresses during this check."})
        if parsed.scheme.lower() == "https" and port in (None, 443):
            tls = _check_tls(host, endpoints)
            network_evidence["tls"] = tls
            network_evidence["tls_status"] = tls["status"]
            if tls["valid"]:
                signals.append({"code": "TLS_CERTIFICATE_VALID", "severity": "info", "text": "A trusted TLS certificate was verified for the website host."})
            elif tls["status"] == "invalid":
                transport_points = max(transport_points, 45)
                signals.append({"code": "TLS_CERTIFICATE_INVALID", "severity": "high", "text": "A trusted TLS certificate could not be verified for this HTTPS website."})
            else:
                signals.append({"code": "TLS_CHECK_UNAVAILABLE", "severity": "info", "text": "The website could not be reached for a TLS check; no TLS risk was added."})
    except ValueError as exc:
        network_evidence["dns_status"] = "private_or_reserved"
        network_points = 70
        signals.append({"code": "NONPUBLIC_DESTINATION", "severity": "high", "text": str(exc)})
    except (OSError, socket.gaierror) as exc:
        network_evidence["dns_status"] = "lookup_failed"
        signals.append({"code": "DNS_LOOKUP_FAILED", "severity": "elevated", "text": "The domain could not be resolved during this check."})

    threat_intel = _check_web_risk(clean_url)
    if threat_intel["status"] == "threat_match":
        signals.append({"code": "THREAT_INTELLIGENCE_MATCH", "severity": "high", "text": "Google Web Risk reported this URL as a known threat."})
    elif threat_intel["status"] == "no_match":
        signals.append({"code": "THREAT_INTELLIGENCE_NO_MATCH", "severity": "info", "text": "Google Web Risk returned no known-threat match for this URL."})

    breakdown = {
        "transport": min(transport_points, 45),
        "domain_structure": min(domain_points, 60),
        "redirect_obfuscation": min(redirect_points, 25),
        "url_structure": min(url_structure_points, 20),
        "nonpublic_destination": network_points,
        "confirmed_threat_match": 100 if threat_intel["status"] == "threat_match" else 0,
    }
    score = max(breakdown["confirmed_threat_match"], min(100, sum(breakdown.values())))
    level = "LOW" if score < 25 else "REVIEW" if score < 60 else "HIGH"

    checks_expected = 2 + (parsed.scheme.lower() == "https")
    checks_completed = int(network_evidence["dns_status"] in {"resolved_public", "private_or_reserved"})
    if parsed.scheme.lower() == "https":
        checks_completed += int(network_evidence["tls_status"] in {"valid", "invalid"})
    checks_completed += int(threat_intel["status"] in {"no_match", "threat_match"})
    coverage = checks_completed / checks_expected
    coverage_level = "HIGH" if coverage >= 1 else "PARTIAL" if coverage >= 0.5 else "LOW"
    core_check_unavailable = network_evidence["dns_status"] == "lookup_failed" or (
        parsed.scheme.lower() == "https" and network_evidence["tls_status"] == "unavailable"
    )
    if score < 25 and core_check_unavailable:
        level = "INDETERMINATE"
    if not signals:
        signals.append({"code": "NO_URL_FLAGS", "severity": "info", "text": "No URL-format warning signals were found by these checks."})

    if level == "INDETERMINATE":
        recommendations = [
            "There is not enough live evidence to rate this website. Pause and verify the merchant through its official app or website.",
            "Check the exact domain spelling and confirm that the payment page belongs to the merchant.",
            "If you cannot verify it, do not enter payment details; use a payment method with buyer protection if you proceed.",
        ]
    elif score >= 60:
        recommendations = [
            "Pause payment and verify the domain through the merchant's official app or website.",
            "Avoid following shortened links or payment redirects from unsolicited messages.",
            "If you continue, use a payment method with buyer protection and verify the payee before confirming.",
        ]
    elif score >= 25:
        recommendations = [
            "Confirm the exact domain through the merchant's official app or a trusted contact channel.",
            "Check the spelling and HTTPS connection before entering payment details.",
            "Review the payee name and payment amount on the provider's confirmation screen.",
        ]
    else:
        recommendations = [
            "Check that the domain spelling matches the merchant you intended to visit.",
            "Confirm the payee and amount on the payment provider's confirmation screen.",
            "Treat this score as limited URL evidence; it does not verify the merchant or transaction.",
        ]

    return {
        "url": clean_url,
        "domain": host,
        "risk_score": score,
        "risk_level": level,
        "score_type": "RULE_BASED_SIGNAL_INDEX",
        "score_breakdown": breakdown,
        "score_explanation": "Rule-based URL signal index (0–100), not a transaction score or calibrated fraud probability. Each component is capped to limit stacked weak clues; a confirmed Web Risk match sets the index to 100. DNS/TLS failures lower evidence coverage instead of adding risk points. Path and query are checked locally, then removed; only the origin is retained and sent to Web Risk.",
        "signals": signals,
        "recommendations": recommendations,
        "evidence": {
            "network": network_evidence,
            "threat_intelligence": threat_intel,
            "coverage": {
                "level": coverage_level,
                "checks_completed": checks_completed,
                "checks_expected": checks_expected,
                "note": "Coverage describes which checks returned a result. A full coverage result does not guarantee the merchant is safe.",
            },
        },
        "reputation_status": {
            "not_configured": "Google Web Risk is not configured; URL, DNS, and TLS checks were used.",
            "unavailable": "Google Web Risk was unavailable; other completed checks are shown.",
            "no_match": "Google Web Risk returned no threat match for this URL.",
            "threat_match": "Google Web Risk returned a threat match for this URL.",
        }[threat_intel["status"]],
    }
