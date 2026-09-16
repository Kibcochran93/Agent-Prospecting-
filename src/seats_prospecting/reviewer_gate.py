"""Run the counting before the judging.

Phase 3, 4 September 2026. The Reviewer runs at high reasoning effort over a
whole batch, and it is the most expensive call in the chain. Spending it on a
batch with two identical subject lines is spending it to learn something a
string comparison already knew.

The mechanical checks in ``differentiation`` are deterministic and free:
identical subject lines, a sentence appearing in two messages, a shared opening
on eight words, an identical closing, a placeholder recipient, an institution
threaded once. Anything they mark FAIL is settled. There is no judgement left to
apply and no verdict a model could return that would change it.

So the gate runs first and refuses. What comes back is the findings, which are
already the rewrite instruction: they name the messages and quote the overlap.

What the gate deliberately does NOT do is grade. A batch that passes counting is
not a good batch, it is a batch worth a reviewer's time. Everything the closed
standard calls a rewrite ground needs judgement, which is why the Reviewer still
exists and why this file cannot replace it.
"""

from __future__ import annotations

from datetime import date

from .differentiation import FAIL, Finding, check, parse_messages, self_report_conflicts
from .review_input import COPY_HEADING


class BatchFailsCounting(Exception):
    """Blocking findings that need no model to establish."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        listed = "\n".join(f"  {f}" for f in findings)
        super().__init__(
            "This batch fails the mechanical checks, so no reviewer run was "
            f"started:\n{listed}\n"
            "These are string comparisons, not judgements. There is no verdict a "
            "reviewer could return that would change them, and the findings above "
            "are already the rewrite instruction. Fix them and re-run the check "
            "with `seats-check-batch` before spending a review."
        )


def is_copy_batch(artifact: str) -> bool:
    """A briefing has nothing for these checks to count."""
    return COPY_HEADING in artifact


def blocking_findings(artifact: str, as_of: date | None = None) -> list[Finding]:
    """Every FAIL the counting produces. Empty for a briefing, by design."""
    if not is_copy_batch(artifact):
        return []
    messages = parse_messages(artifact)
    if not messages:
        # The extractor found a copy heading and the parser found no messages.
        # That is a harness fault, not a copy fault, and a reviewer run on an
        # empty artifact returns a confident verdict about nothing.
        return [
            Finding(
                FAIL,
                "no-messages-parsed",
                "the artifact has a copy heading but no messages were parsed; "
                "check the extractor before reviewing",
            )
        ]
    findings = check(messages, as_of=as_of)
    findings += self_report_conflicts(artifact, findings)
    return [f for f in findings if f.severity == FAIL]


def gate(artifact: str, as_of: date | None = None) -> None:
    """Raise when the batch fails counting. Returns None when it is worth a run."""
    findings = blocking_findings(artifact, as_of=as_of)
    if findings:
        raise BatchFailsCounting(findings)
