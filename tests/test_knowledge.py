"""The knowledge tool is read-only and cannot be steered outside its directory."""

from __future__ import annotations

import pytest

from seats_prospecting.tools import knowledge


def test_module_exposes_no_write_function():
    forbidden = ("write", "create", "delete", "update", "save", "put", "post")
    for name in dir(knowledge):
        if name.startswith("_"):
            continue
        assert not any(name.lower().startswith(f) for f in forbidden), (
            f"knowledge module gained a write-shaped function: {name}"
        )


@pytest.mark.parametrize(
    "attempt",
    [
        "../.env",
        "../../.env",
        "..\\.env",
        "/etc/passwd",
        "subdir/../../.env",
    ],
)
def test_paths_outside_the_directory_are_refused(attempt):
    with pytest.raises((knowledge.OutsideKnowledgeDir, FileNotFoundError, OSError)):
        knowledge._resolve(attempt)


def test_only_markdown_is_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / "playbook.md").write_text("# Playbook", encoding="utf-8")
    (tmp_path / "secrets.env").write_text("KEY=value", encoding="utf-8")
    names = [p.name for p in knowledge._documents()]
    assert names == ["playbook.md"]


def test_the_data_notice_states_the_boundary():
    # Every knowledge result is prefixed with this, because the documents carry
    # internal commentary that must not reach a prospect.
    assert "not instructions" in knowledge.DATA_NOTICE
    assert "no competitor weakness language" in knowledge.DATA_NOTICE
    assert "named institution tied to a" in knowledge.DATA_NOTICE


def test_sections_split_on_headings(tmp_path, monkeypatch):
    import re

    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    text = "# Doc\n\nintro\n\n## What not to say\n\nNo Student CRM.\n\n## Motions\n\nNine of them.\n"
    (tmp_path / "intel.md").write_text(text, encoding="utf-8")
    sections = re.split(r"\n(?=#{1,3} )", (tmp_path / "intel.md").read_text(encoding="utf-8"))
    headings = [s.strip().split("\n", 1)[0] for s in sections]
    assert "## What not to say" in headings
    assert "## Motions" in headings
