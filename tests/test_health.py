from stackline_crawling.contracts import ContractError, Thresholds, validate_result
from stackline_crawling.health import evaluate_health


def metrics(products=10, prices=10, failed=0):
    return {
        "pages_discovered": 2,
        "pages_visited": 2,
        "pages_failed": failed,
        "products_extracted": products,
        "products_with_price": prices,
        "categories_extracted": 1,
        "product_links_found": products,
        "pagination_links_found": 1,
        "duplicate_products": 0,
        "blocked_or_challenge_detected": False,
        "response_size_bytes": 1000,
        "html_fingerprint": "sha256:test",
        "duration_ms": 10,
    }


def test_first_run_is_accepted():
    health = evaluate_health(metrics(), None, Thresholds())
    assert health["state"] == "first_run"


def test_empty_result_fails_quality():
    health = evaluate_health(metrics(products=0, prices=0), None, Thresholds())
    assert health["state"] == "failed"


def test_result_contract_rejects_missing_metric():
    result = {
        "schema_version": 1, "run_id": "r", "status": "COMPLETED",
        "data": {"categories": [], "products": []}, "metrics": {},
        "quality": {"state": "first_run"}, "evidence": [], "error": None,
    }
    try:
        validate_result(result)
    except ContractError:
        return
    raise AssertionError("El contrato debía rechazar métricas faltantes")
