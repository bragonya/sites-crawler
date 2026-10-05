from __future__ import annotations

import re
from pathlib import Path


MAX_SITE_SOP_BYTES = 24_000
MAX_SECTION_CHARS = 4_000
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _validate_section(section: str) -> str:
    normalized = _slug(section)
    if not normalized or len(normalized) > 80:
        raise ValueError("La sección del SOP no es válida")
    return normalized


def sop_index(path: Path) -> list[dict[str, int | str]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    matches = list(_SECTION_RE.finditer(text))
    index: list[dict[str, int | str]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        title = match.group(1).strip()
        index.append({"section": _slug(title), "title": title, "chars": end - match.end()})
    return index


def read_sop_section(path: Path, section: str, max_chars: int = 6_000) -> dict[str, object]:
    requested = _validate_section(section)
    if not path.is_file():
        return {"section": requested, "found": False, "content": ""}
    text = path.read_text(encoding="utf-8")
    matches = list(_SECTION_RE.finditer(text))
    for position, match in enumerate(matches):
        title = match.group(1).strip()
        if _slug(title) != requested:
            continue
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        content = text[match.end():end].strip()
        return {
            "section": requested,
            "title": title,
            "found": True,
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }
    return {"section": requested, "found": False, "content": ""}


def update_sop_section(path: Path, section: str, content: str) -> dict[str, object]:
    requested = _validate_section(section)
    if len(content) > MAX_SECTION_CHARS:
        raise ValueError(f"Una sección del SOP no puede exceder {MAX_SECTION_CHARS} caracteres")
    title = requested.replace("-", " ").title()
    replacement = f"## {title}\n\n{content.strip()}\n"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    matches = list(_SECTION_RE.finditer(existing))
    updated = existing
    for position, match in reversed(list(enumerate(matches))):
        if _slug(match.group(1).strip()) != requested:
            continue
        end = matches[position + 1].start() if position + 1 < len(matches) else len(existing)
        updated = existing[:match.start()] + replacement + existing[end:]
        break
    else:
        updated = (existing.rstrip() + "\n\n" if existing.strip() else "") + replacement
    if len(updated.encode("utf-8")) > MAX_SITE_SOP_BYTES:
        raise ValueError(f"El SOP del sitio no puede exceder {MAX_SITE_SOP_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return {"section": requested, "bytes": len(updated.encode("utf-8")), "index": sop_index(path)}
