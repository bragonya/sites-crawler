from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ContractError(ValueError):
    """El resultado no cumple el contrato estricto del crawling."""


REQUIRED_METRICS = {
    "pages_discovered",
    "pages_visited",
    "pages_failed",
    "products_extracted",
    "products_with_price",
    "categories_extracted",
    "product_links_found",
    "pagination_links_found",
    "duplicate_products",
    "blocked_or_challenge_detected",
    "response_size_bytes",
    "html_fingerprint",
    "duration_ms",
}


@dataclass(frozen=True)
class Thresholds:
    min_products: int = 1
    min_price_coverage_ratio: float = 0.80
    max_failed_page_ratio: float = 0.20
    max_duplicate_product_rate: float = 0.05
    baseline_degraded_ratio: float = 0.70
    baseline_failed_ratio: float = 0.50

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "Thresholds":
        values = payload or {}
        return cls(
            min_products=int(values.get("min_products", cls.min_products)),
            min_price_coverage_ratio=float(values.get("min_price_coverage_ratio", cls.min_price_coverage_ratio)),
            max_failed_page_ratio=float(values.get("max_failed_page_ratio", cls.max_failed_page_ratio)),
            max_duplicate_product_rate=float(values.get("max_duplicate_product_rate", cls.max_duplicate_product_rate)),
            baseline_degraded_ratio=float(values.get("baseline_degraded_ratio", cls.baseline_degraded_ratio)),
            baseline_failed_ratio=float(values.get("baseline_failed_ratio", cls.baseline_failed_ratio)),
        )


def validate_result(result: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        raise ContractError("El resultado debe ser un objeto JSON")
    for key in ("schema_version", "run_id", "status", "data", "metrics", "quality", "error"):
        if key not in result:
            raise ContractError(f"Falta el campo obligatorio: {key}")
    if result["schema_version"] != 1:
        raise ContractError("schema_version debe ser 1")
    if result["status"] not in {"COMPLETED", "EXECUTION_FAILED", "QUALITY_FAILED"}:
        raise ContractError("status no reconocido")
    data = result["data"]
    if not isinstance(data, dict) or not isinstance(data.get("products"), list) or not isinstance(data.get("categories"), list):
        raise ContractError("data debe contener products y categories como listas")
    metrics = result["metrics"]
    if not isinstance(metrics, dict) or not REQUIRED_METRICS.issubset(metrics):
        missing = sorted(REQUIRED_METRICS - set(metrics if isinstance(metrics, dict) else {}))
        raise ContractError(f"Faltan métricas obligatorias: {missing}")
    for key in ("pages_discovered", "pages_visited", "pages_failed", "products_extracted", "products_with_price",
                "categories_extracted", "product_links_found", "pagination_links_found", "duplicate_products",
                "response_size_bytes", "duration_ms"):
        if not isinstance(metrics[key], int) or metrics[key] < 0:
            raise ContractError(f"La métrica {key} debe ser entero no negativo")
    if not isinstance(metrics["blocked_or_challenge_detected"], bool):
        raise ContractError("blocked_or_challenge_detected debe ser booleano")
    if not isinstance(metrics["html_fingerprint"], str) or not metrics["html_fingerprint"]:
        raise ContractError("html_fingerprint es obligatorio")
    quality = result["quality"]
    if not isinstance(quality, dict) or quality.get("state") not in {"healthy", "degraded", "failed", "first_run"}:
        raise ContractError("quality.state no reconocido")
    if result["status"] == "COMPLETED" and quality["state"] not in {"healthy", "first_run"}:
        raise ContractError("COMPLETED requiere health healthy o first_run")
    for product in data["products"]:
        if not isinstance(product, dict) or not str(product.get("name", "")).strip():
            raise ContractError("Cada producto debe tener name")
