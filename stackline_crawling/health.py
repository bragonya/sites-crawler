from __future__ import annotations

from typing import Any

from .contracts import Thresholds


def evaluate_health(metrics: dict[str, Any], baseline_products: int | None, thresholds: Thresholds) -> dict[str, Any]:
    products = int(metrics["products_extracted"])
    pages = int(metrics["pages_visited"])
    failed_pages = int(metrics["pages_failed"])
    prices = int(metrics["products_with_price"])
    duplicates = int(metrics["duplicate_products"])
    price_coverage = prices / products if products else 0.0
    failed_page_ratio = failed_pages / pages if pages else 1.0
    duplicate_rate = duplicates / products if products else 0.0
    baseline_ratio = None if baseline_products in (None, 0) else products / baseline_products

    reasons: list[str] = []
    anomalies: list[dict[str, str]] = []
    critical = False
    if products < thresholds.min_products:
        reasons.append("products_extracted is below min_products")
        anomalies.append({"code": "no_products", "severity": "critical"})
        critical = True
    if metrics["blocked_or_challenge_detected"]:
        reasons.append("Login, captcha, or challenge detected")
        anomalies.append({"code": "blocked_or_challenge", "severity": "critical"})
        critical = True
    if price_coverage < thresholds.min_price_coverage_ratio:
        reasons.append("Price coverage is below the threshold")
        anomalies.append({"code": "price_coverage_low", "severity": "warning" if price_coverage >= 0.50 else "critical"})
        critical = critical or price_coverage < 0.50
    if failed_page_ratio > thresholds.max_failed_page_ratio:
        reasons.append("Failed-page ratio exceeds the threshold")
        anomalies.append({"code": "page_failure_rate", "severity": "warning" if failed_page_ratio <= 0.50 else "critical"})
        critical = critical or failed_page_ratio > 0.50
    if duplicate_rate > thresholds.max_duplicate_product_rate:
        reasons.append("Duplicate-product ratio exceeds the threshold")
        anomalies.append({"code": "duplicate_rate", "severity": "critical" if duplicate_rate > 0.20 else "warning"})
        critical = critical or duplicate_rate > 0.20
    if baseline_ratio is not None and baseline_ratio < thresholds.baseline_failed_ratio:
        reasons.append("Coverage dropped more than allowed versus baseline")
        anomalies.append({"code": "coverage_drop", "severity": "critical"})
        critical = True
    elif baseline_ratio is not None and baseline_ratio < thresholds.baseline_degraded_ratio:
        reasons.append("Coverage dropped versus baseline")
        anomalies.append({"code": "coverage_drop", "severity": "warning"})

    state = "failed" if critical else "degraded" if reasons else ("first_run" if baseline_ratio is None else "healthy")
    score = min(
        price_coverage,
        max(0.0, 1.0 - failed_page_ratio),
        max(0.0, 1.0 - duplicate_rate),
        1.0 if baseline_ratio is None else max(0.0, min(1.0, baseline_ratio)),
    )
    return {
        "state": state,
        "score": round(score, 4),
        "threshold": 0.80,
        "reasons": reasons,
        "anomalies": anomalies,
        "derived": {
            "price_coverage_ratio": round(price_coverage, 4),
            "failed_page_ratio": round(failed_page_ratio, 4),
            "duplicate_product_rate": round(duplicate_rate, 4),
            "coverage_vs_baseline_ratio": None if baseline_ratio is None else round(baseline_ratio, 4),
        },
    }
