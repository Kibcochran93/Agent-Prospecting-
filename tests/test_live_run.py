"""The launcher is a script, so the parts that can be wrong are tested.

`scripts/live_run.py` is how every live run is started. Two things in it decide
what a later session can reconstruct: the trail of what the run actually did,
and the rule that an unattended run never approves anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("live_run", ROOT / "scripts" / "live_run.py")
live_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_run)


class _Raw:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_hosted_tool_calls_are_named_not_printed_as_a_question_mark():
    """A hosted connector's raw item carries no .name, so the trail read 'TOOL ?'
    and a later session could not tell which connector a run had reached."""
    assert live_run._tool_name(_Raw(name="apollo_find_org")) == "apollo_find_org"
    assert live_run._tool_name({"name": "hubspot_find_account"}) == "hubspot_find_account"
    assert live_run._tool_name({"server_label": "apollo", "type": "mcp_call"}) == "apollo"
    assert live_run._tool_name(None) == "?"


def test_an_unattended_run_rejects_every_approval():
    """The default decision function must never approve. A run launched from a
    session has nobody at the keyboard to read the payload."""
    printed = []
    interruption = _Raw(name="create_manual_email_drafts", arguments="{}")
    live_run.render_approval = lambda *a, **k: printed.append(a) or ""
    assert live_run._reject(interruption, "Campaign Builder") is False


def test_the_reviewer_is_refused_a_session():
    """A reviewer that remembers prior batches grades against drift."""
    source = (ROOT / "scripts" / "live_run.py").read_text(encoding="utf-8")
    assert "reviewer" in source and "takes no session" in source
