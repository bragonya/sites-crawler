from pathlib import Path

import pytest

from stackline_crawling.sop_runtime import read_sop_section, sop_index, update_sop_section


def test_sop_updates_replace_sections_and_keep_compact_index(tmp_path: Path):
    path = tmp_path / "site-sop.md"
    update_sop_section(path, "pagination", "Usar el enlace next y no repetir URLs.")
    update_sop_section(path, "pagination", "Seguir next hasta que no exista.")

    text = path.read_text(encoding="utf-8")
    assert text.count("## Pagination") == 1
    assert "Usar el enlace" not in text
    assert read_sop_section(path, "pagination")["content"] == "Seguir next hasta que no exista."
    assert len(sop_index(path)) == 1


def test_sop_rejects_oversized_section(tmp_path: Path):
    with pytest.raises(ValueError, match="4000"):
        update_sop_section(tmp_path / "site-sop.md", "quirks", "x" * 4_001)
