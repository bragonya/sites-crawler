from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup


_PRICE = re.compile(r"(?i)(?:GTQ|Q)\s*([0-9]{1,3}(?:[,.]\s*[0-9]{3})*(?:[.,]\s*[0-9]{1,2})?)")
_PAGE = re.compile(r"/page/\d+(?:/|$)")
_PRODUCT = re.compile(r"/shop/(?!category/).+?-\d+$")
_CATEGORY = re.compile(r"^/shop/category/[^/]+-\d+/?$")
_CATALOG = {"/shop", "/shop/", "/shopby-category", "/shopby-category/"}


def _public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
        return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)
    except ValueError:
        return parsed.hostname not in {"localhost", "host.docker.internal"}


def _canonical(url: str, base: str) -> str:
    absolute = urljoin(base, url)
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def _price(text: str) -> float | None:
    match = _PRICE.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip().replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if re.search(r",\d{1,2}$", raw) else raw.replace(",", "")
    elif "." in raw and not re.search(r"\.\d{1,2}$", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _category(document: BeautifulSoup) -> str:
    heading = document.select_one("h1")
    return " ".join(heading.get_text(" ", strip=True).split()) if heading else ""


class PublicSiteCrawler:
    def __init__(self, build_root: Path, timeout_seconds: int = 20) -> None:
        self.build_root = build_root
        self.timeout_seconds = timeout_seconds

    def crawl(self, site_url: str, max_pages: int | None = None) -> dict:
        if not _public_http_url(site_url):
            raise ValueError("site_url debe ser HTTP(S) público")
        started = time.monotonic()
        base_host = urlparse(site_url).netloc
        queue: deque[str] = deque([_canonical(site_url, site_url)])
        discovered = set(queue)
        visited: list[str] = []
        failed: list[dict[str, str]] = []
        rows: dict[str, dict] = {}
        categories: set[str] = set()
        response_size = 0
        pagination_links = 0
        product_links = 0
        page_number = 0
        html_dir = self.build_root / "evidence" / "html"
        html_dir.mkdir(parents=True, exist_ok=True)

        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers={"User-Agent": "StacklineCrawler/1.0"}) as client:
            while queue and (max_pages is None or len(visited) < max_pages):
                page_url = queue.popleft()
                if page_url in visited:
                    continue
                visited.append(page_url)
                page_number += 1
                try:
                    response = client.get(page_url)
                    response.raise_for_status()
                    html = response.text
                    response_size += len(response.content)
                    (html_dir / f"page-{page_number:04d}.html").write_text(html, encoding="utf-8")
                    document = BeautifulSoup(html, "html.parser")
                    category = _category(document)
                    if category:
                        categories.add(category)
                    for link in document.select("a[href]"):
                        candidate = _canonical(link.get("href", ""), page_url)
                        parsed = urlparse(candidate)
                        if _PRODUCT.search(parsed.path):
                            candidate = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                            parsed = urlparse(candidate)
                        if parsed.netloc != base_host:
                            continue
                        if _PRODUCT.search(parsed.path):
                            product_links += 1
                            card = link.find_parent(["article", "li", "form"]) or link.parent
                            text = card.get_text(" ", strip=True) if card else link.get_text(" ", strip=True)
                            name = link.get_text(" ", strip=True) or (card.select_one("h6,h5,h4").get_text(" ", strip=True) if card and card.select_one("h6,h5,h4") else "")
                            if name:
                                rows.setdefault(candidate, {
                                    "name": " ".join(name.split()),
                                    "unitCost": _price(text),
                                    "currency": "GTQ",
                                    "sourceUrl": page_url,
                                    "productUrl": candidate,
                                    "categoryPath": [category] if category else [],
                                    "supplierSku": re.search(r"-(\d+)$", parsed.path).group(1) if re.search(r"-(\d+)$", parsed.path) else None,
                                    "attributes": {"crawler": "stackline-crawling"},
                                })
                        if _PAGE.search(parsed.path):
                            pagination_links += 1
                        should_follow = (
                            parsed.path in _CATALOG
                            or _CATEGORY.search(parsed.path) is not None
                            or _PAGE.search(parsed.path) is not None
                        )
                        if should_follow and candidate not in discovered:
                            discovered.add(candidate)
                            queue.append(candidate)
                except Exception as error:  # noqa: BLE001
                    failed.append({"url": page_url, "error": str(error)})

        products = list(rows.values())
        identities = {
            item.get("supplierSku") or item.get("productUrl") or item.get("name")
            for item in products
        }
        duplicate_products = max(0, len(products) - len(identities))
        fingerprint = hashlib.sha256("|".join(sorted(visited)).encode()).hexdigest()
        return {
            "categories": [{"name": value} for value in sorted(categories)],
            "products": products,
            "metrics": {
                "pages_discovered": len(discovered),
                "pages_visited": len(visited),
                "pages_failed": len(failed),
                "products_extracted": len(products),
                "products_with_price": sum(1 for item in products if item.get("unitCost") is not None),
                "categories_extracted": len(categories),
                "product_links_found": product_links,
                "pagination_links_found": pagination_links,
                "duplicate_products": duplicate_products,
                "blocked_or_challenge_detected": any(word in str(failed).lower() for word in ("captcha", "login", "forbidden", "403")),
                "response_size_bytes": response_size,
                "html_fingerprint": f"sha256:{fingerprint}",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "failed_urls": failed,
                "visited_urls": visited,
                "discovered_urls": sorted(discovered),
            },
        }
