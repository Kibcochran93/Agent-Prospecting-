"""Build the Reviewer's input from a run log, structurally.

Why this module exists. Both reviewer passes so far were fed an artifact
assembled by hand, by concatenating the Campaign Builder's checkpoint logs. That
carried the producing agent's operator-facing lines into the review object:

    "Confirm the tighter, recommended slice before checkpoint 4 buying-group
     mapping."
    "The copy below must not be used until CRM suppression is complete."
    "Names require manual personalization."

The Reviewer flagged them both times, correctly, under its artifact-integrity
rule, and each pass ended on that finding before the copy had been graded on its
own terms. The bug was in the harness, not the Reviewer and not the copy.

The fix is structural rather than a note in a runbook telling whoever assembles
the input to be careful. A run log goes in; only the message blocks and the
producing agent's own differentiation check come out. Checkpoint prose, list
build logic, dispatch lines and the CLI's run audit have no path through.

Two further properties matter as much as the filtering:

* **The frame is a constant.** ``FRAME`` is fixed text. Nothing about this run,
  this account, or any prior verdict can be attached to it. The second pass was
  told a prior review had returned a rewrite, which primes the reviewer against
  the standard it is supposed to grade to.
* **A leak is a hard failure, not a warning.** If workflow vocabulary survives
  into the extracted text, the artifact is refused and the offending lines are
  named. A refused artifact costs nothing; a review pass spent on a leak costs a
  run.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FRAME_OPEN = "Review the copy batch below."

# Appended only when the producing agent's own check is in the artifact. Kib's
# rule, 3 September: the self-check stays and stops counting as evidence. The
# Reviewer still has to see it, because a disagreement between the two is worth
# reading, but it is a claim about the batch rather than a finding about it.
FRAME_SELF_CHECK = (
    "The final section is the producing agent's own answers about its batch. "
    "That is a claim, not evidence. Grade the copy, not the claim, and say so "
    "where the two disagree."
)
FRAME_OPEN_BRIEFING = "Review the briefing below."

# `scripts/live_run.py` prints TRAIL, AUDIT, REVEALS and LAST AGENT above the
# model's output. The AUDIT line contains the work order verbatim, so handing a
# raw run log to the Reviewer would put the Director's dispatch inside the
# object under review. Everything before this marker is harness, not artifact.
OUTPUT_MARKER = "=== OUTPUT ==="

# The headings the artifact carries, whatever the producing agent called its
# sections. The first live M1 batch arrived under "## Checkpoint 5: Manual-paste
# copy", so the heading itself was workflow prose and the build refused a batch
# that was otherwise clean. A canonical heading also means the Reviewer sees the
# same two labels every time rather than whatever this run happened to name them.
COPY_HEADING = "## Copy batch"
CHECK_HEADING = "## The producing agent's differentiation check"
ARTIFACT_BEGINS = "--- ARTIFACT BEGINS ---"
ARTIFACT_ENDS = "--- ARTIFACT ENDS ---"

# The CLI prints these after the model output. Everything from the first one on
# is harness noise, not artifact.
_TRAILER = re.compile(r"^(--- run audit ---|dispatched to:)", re.MULTILINE)

_H2 = re.compile(r"^## +(?P<title>.+?)\s*$", re.MULTILINE)
_H3 = re.compile(r"^### +.+?$", re.MULTILINE)

_COPY_HEADING = re.compile(r"\b(batch|copy|messages|touches)\b", re.IGNORECASE)

# The signature of a message block. A briefing's "One draft" section is a single
# letter with a salutation; a copy batch addresses each message to a role.
# The colon is optional because the live batches write it both ways:
# "**To: Provost**" and "**To Rachel Broussard, Coordinator of Events**".
_ADDRESSED = re.compile(r"^\*\*To:?\s", re.MULTILINE)
_CHECK_HEADING = re.compile(r"\bdifferentiation\b", re.IGNORECASE)

# 11 September 2026, the Arkansas Mountain Home run. With no "## Copy batch"
# heading to section on -- nothing in campaign.md actually tells the producing
# agent to write one, it only prescribes the "### / **To:** / **Subject:**"
# header -- the fallback path in _build_copy kept the whole preamble verbatim,
# sign-off and all. That live batch closed with "**Motion remains Unclear:**
# ... **Status:** Draft only, not cleared to send.", which landed inside the
# same block as the email body: the exact artifact-integrity failure this
# module exists to catch, in a shape its section-based stripping never reaches,
# because there was no section boundary to strip at.
#
# The copy standard's body is plain, contraction-heavy prose; the only bold
# markup a real message ever carries is its own "**To:**" and "**Subject:**"
# header line. So a later paragraph that opens with bold formatting and is not
# that header is the producing agent talking again, not more of the message.
_BOLD_LED = re.compile(r"^\*\*\S")
_SUBJECT_FIELD = re.compile(r"^\*\*Subject:\*\*\s", re.MULTILINE)


def _is_header_field(line: str) -> bool:
    return bool(_ADDRESSED.match(line) or _SUBJECT_FIELD.match(line))


def _drop_trailing_notes(text: str) -> str:
    """Cut a headerless message block off before a bold-led sign-off.

    Only used on the fallback path (no H2 heading at all to section on).
    Paragraph by paragraph, keeping the header fields and ordinary prose; once
    a later paragraph opens with bold formatting that is not the To or Subject
    field, everything from there to the end is dropped. Same reasoning
    ``_answers_only`` and ``_BOOKKEEPING`` already use elsewhere in this file:
    a producing agent's sign-off is bookkeeping addressed to Kib, not part of
    the object under review.
    """
    paragraphs = text.split("\n\n")
    last_content = -1
    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            continue
        first_line = stripped.splitlines()[0]
        if _BOLD_LED.match(first_line) and not _is_header_field(first_line):
            continue
        last_content = i
    if last_content == -1:
        return text.strip()
    return "\n\n".join(paragraphs[: last_content + 1]).strip()


# Workflow vocabulary, split by what is under review.
#
# A copy batch is customer-facing text: a message to a Provost has no business
# naming Apollo, HubSpot or the ledger, so those are leaks. A briefing is
# internal by design. Its "Internal context" section is supposed to say who owns
# the account in HubSpot and which ledger file was written, and the Reviewer's
# briefing checklist grades exactly that. So the briefing profile keeps only the
# terms that mean workflow prose reached the artifact.
WORKFLOW_TERMS: tuple[str, ...] = (
    "checkpoint",
    "work order",
    # Regex, because "staff handoffs" is ordinary higher education language and
    # "hand back to the Director" is not. A plain substring failed a clean batch
    # on 4 September for a sentence about students moving between staff.
    "re:\\bhand ?back\\b",
    "re:\\bhandoff to\\b",
    "approval gate",
    "approve the copy",
    "approve the batch",
    "reviewer",
    "run audit",
    "dispatched to",
    "manual paste",
    "manual personalization",
    "manual personalisation",
    "crm suppression",
    "sequence write",
    "create_manual_email_drafts",
    "enroll contacts",
)

_SYSTEM_TERMS: tuple[str, ...] = (
    "pending owner review",
    "apollo",
    "hubspot",
    "notion",
    "sharepoint",
    "ledger",
    "battlecard",
    "playbook",
)

# Run bookkeeping a briefing signs off with. Not a directive, not content.
_BOOKKEEPING = re.compile(
    r"^\s*(\*\*)?(ledger\b|it is awaiting|no sequence was created)",
    re.IGNORECASE,
)

# The copy profile: workflow prose plus the system names. A hit means checkpoint
# prose reached the copy, or the copy is talking about the system that produced
# it. Either way it is a finding for Kib before it is a finding for the Reviewer,
# so the build fails and names the line.
#
# Deliberately narrow. Words a real outreach email might plausibly use, such as
# "approval" on its own, are not on it; the cost of a false positive is a refused
# build, and the list stays worth trusting only while that is rare.
DIRECTIVE_TERMS: tuple[str, ...] = WORKFLOW_TERMS + _SYSTEM_TERMS


class ReviewInputError(Exception):
    """Base for every refusal. The reviewer run does not start on any of them."""


class NoCopyFound(ReviewInputError):
    """The log has no copy batch section, so there is nothing to review."""


class DirectiveLeak(ReviewInputError):
    """Workflow vocabulary survived into the artifact."""

    def __init__(self, hits: list[tuple[int, str, str]]):
        self.hits = hits
        lines = "\n".join(f"  line {n}: {term!r} in {text.strip()!r}" for n, term, text in hits)
        super().__init__(
            "Refusing to build a review artifact that still contains workflow "
            f"language:\n{lines}\n"
            "Fix it at the source. These lines are the producing agent talking to "
            "Kib, and a Reviewer that sees them fails the artifact on that alone."
        )


@dataclass
class ReviewArtifact:
    text: str
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _strip_harness(log: str) -> str:
    """Drop everything the launcher printed above the model's output.

    The AUDIT line carries the Director's work order verbatim. A raw run log
    handed to the Reviewer therefore contains the dispatch that produced the
    thing under review, which is the whole failure this module exists to stop.
    """
    marker = log.rfind(OUTPUT_MARKER)
    return log[marker + len(OUTPUT_MARKER) :] if marker != -1 else log


def _strip_trailer(log: str) -> str:
    match = _TRAILER.search(log)
    return log[: match.start()] if match else log


def _strip_markers(log: str) -> str:
    out = []
    for line in log.splitlines():
        if line.strip() in (ARTIFACT_BEGINS, ARTIFACT_ENDS):
            continue
        out.append(line)
    return "\n".join(out)


def _sections(log: str) -> list[tuple[str, str]]:
    """Split on H2 headings. Text before the first one is returned by _preamble."""
    matches = list(_H2.finditer(log))
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(log)
        sections.append((match.group("title"), log[match.end() : end].strip("\n")))
    return sections


def _preamble(log: str) -> str:
    """Everything above the first H2.

    Usually checkpoint prose worth dropping. Sometimes the batch itself: on the
    4 September run the Campaign Builder wrote its five messages straight into
    H3 institution sections with no H2 above them, so the only H2 in the log was
    the differentiation check. The extractor kept the check, dropped the copy,
    and the checker reported zero messages parsed. A checker that silently finds
    nothing is worse than one that fails, so the preamble is now looked at.
    """
    first = _H2.search(log)
    return log[: first.start()] if first else log


def _messages_only(body: str) -> str:
    """Drop a copy section's preamble.

    The producing agent writes a line or two above the first institution saying
    what the batch is and what still needs doing by hand. That is a note to Kib.
    The messages start at the first H3.
    """
    first = _H3.search(body)
    return body[first.start() :].strip("\n") if first else body.strip("\n")


_NUMBERED = re.compile(r"^\s*\d+\.\s")


def _answers_only(body: str) -> str:
    """Keep the five answers of the differentiation check and nothing after them.

    The producing agent signs off under the same heading: what it did about a
    sequence, which ledger file it dropped. Run bookkeeping, addressed to Kib.
    The check itself is a fixed shape, five numbered answers, so the shape is
    what the extractor keeps.
    """
    lines = body.splitlines()
    numbered = [i for i, line in enumerate(lines) if _NUMBERED.match(line)]
    if not numbered:
        return body.strip("\n")
    return "\n".join(lines[numbered[0] : numbered[-1] + 1]).strip("\n")


def _matches(term: str, lowered: str) -> bool:
    """A term is a substring, or a regular expression when prefixed with re:."""
    if term.startswith("re:"):
        return re.search(term[3:], lowered) is not None
    return term in lowered


def scan_for_directives(
    text: str, terms: tuple[str, ...] = DIRECTIVE_TERMS
) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.casefold()
        for term in terms:
            if _matches(term, lowered):
                hits.append((number, term, line))
                break
    return hits


def _looks_like_copy(sections: list[tuple[str, str]], cleaned: str) -> bool:
    """Two signals, both required.

    The heading alone misreads a briefing: section 7 of every briefing is called
    "One draft", which is a letter with a salutation, not a batch. So a copy log
    also has to address its messages, which is what the Campaign Builder does and
    a briefing does not.
    """
    heading = any(
        _COPY_HEADING.search(title) or _CHECK_HEADING.search(title)
        for title, _ in sections
    )
    return heading and bool(_ADDRESSED.search(cleaned))


def build(log: str, kind: str = "auto") -> ReviewArtifact:
    """Extract the reviewable object from a run log.

    ``kind`` is "copy", "briefing", or "auto". Auto reads a copy batch when the
    log has a batch or differentiation heading and a briefing otherwise, which
    is what the two agents actually produce.
    """
    cleaned = _strip_markers(_strip_trailer(_strip_harness(log)))
    sections = _sections(cleaned)

    if kind == "auto":
        kind = "copy" if _looks_like_copy(sections, cleaned) else "briefing"
        if kind == "briefing" and _ADDRESSED.search(_preamble(cleaned)):
            kind = "copy"  # addressed messages with no heading at all
    if kind not in ("copy", "briefing"):
        raise ReviewInputError(f"Unknown kind {kind!r}. Use 'copy' or 'briefing'.")

    if kind == "briefing":
        return _build_briefing(sections)
    return _build_copy(sections, _preamble(cleaned))


def _build_copy(sections: list[tuple[str, str]], preamble: str = "") -> ReviewArtifact:
    kept_parts: list[str] = []
    kept: list[str] = []
    dropped: list[str] = []
    found_copy = False

    for title, body in sections:
        if _CHECK_HEADING.search(title):
            kept.append(title)
            kept_parts.append(f"{CHECK_HEADING}\n\n{_answers_only(body)}")
        elif _COPY_HEADING.search(title):
            kept.append(title)
            found_copy = True
            kept_parts.append(f"{COPY_HEADING}\n\n{_messages_only(body).strip()}")
        else:
            dropped.append(title)

    if not found_copy and _ADDRESSED.search(preamble):
        # The messages are above the first heading. Keep them, from the first
        # institution or the first addressed line, and put them under the
        # canonical heading like any other batch. No H2 boundary exists here to
        # strip a trailing sign-off at, so _drop_trailing_notes does it instead.
        first = _H3.search(preamble) or _ADDRESSED.search(preamble)
        kept.insert(0, "(messages above the first heading)")
        kept_parts.insert(
            0, f"{COPY_HEADING}\n\n{_drop_trailing_notes(preamble[first.start():])}"
        )

    if not kept_parts:
        raise NoCopyFound(
            "No copy batch section in this log. Looked for an H2 heading naming a "
            f"batch, copy, messages or drafts. Found: {dropped or 'no H2 headings at all'}."
        )

    artifact = "\n\n".join(kept_parts).strip()
    hits = scan_for_directives(artifact)
    if hits:
        raise DirectiveLeak(hits)

    frame = FRAME_OPEN
    if CHECK_HEADING in artifact:
        frame = f"{FRAME_OPEN} {FRAME_SELF_CHECK}"
    text = f"{frame}\n\n{ARTIFACT_BEGINS}\n\n{artifact}\n\n{ARTIFACT_ENDS}\n"
    return ReviewArtifact(text=text, kept=kept, dropped=dropped)


def _build_briefing(sections: list[tuple[str, str]]) -> ReviewArtifact:
    """A briefing is reviewed whole.

    Every section of it is under the Reviewer's briefing checklist: sources and
    dates, what was left unverified, the buying group, the discovery questions,
    and whether the draft carries anything internal. So nothing is filtered out
    by heading. What goes is the run's own sign-off, which is bookkeeping rather
    than research, and the scan runs on the workflow terms only, because a
    briefing is supposed to name HubSpot and the ledger.
    """
    if not sections:
        raise NoCopyFound(
            "No sections in this log. A briefing is read by its H2 headings and "
            "this one has none."
        )

    kept: list[str] = []
    parts: list[str] = []
    for title, body in sections:
        lines = [line for line in body.splitlines() if not _BOOKKEEPING.match(line)]
        kept.append(title)
        parts.append(f"## {title}\n\n" + "\n".join(lines).strip())

    artifact = "\n\n".join(parts).strip()
    hits = scan_for_directives(artifact, terms=WORKFLOW_TERMS)
    if hits:
        raise DirectiveLeak(hits)

    text = (
        f"{FRAME_OPEN_BRIEFING}\n\n{ARTIFACT_BEGINS}\n\n{artifact}\n\n{ARTIFACT_ENDS}\n"
    )
    return ReviewArtifact(text=text, kept=kept, dropped=[])


def build_from_path(path: str | Path, kind: str = "auto") -> ReviewArtifact:
    return build(Path(path).read_text(encoding="utf-8", errors="replace"), kind=kind)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seats-review-input",
        description=(
            "Build the Reviewer's input from a Campaign Builder run log. Writes the "
            "artifact to stdout and what it kept and dropped to stderr."
        ),
    )
    parser.add_argument("log", help="Path to the run log.")
    parser.add_argument(
        "--kind",
        choices=("auto", "copy", "briefing"),
        default="auto",
        help="What the log holds. Auto reads a batch heading as copy, else a briefing.",
    )
    parser.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help=(
            "Write the artifact to this file as UTF-8. Prefer it to shell "
            "redirection: PowerShell's '>' writes UTF-16, which the Reviewer's "
            "stdin then reads as UTF-8 and mangles."
        ),
    )
    args = parser.parse_args(argv)

    try:
        artifact = build_from_path(args.log, kind=args.kind)
    except ReviewInputError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print(f"kept: {', '.join(artifact.kept)}", file=sys.stderr)
    print(f"dropped: {', '.join(artifact.dropped) or 'nothing'}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(artifact.text, encoding="utf-8")
        print(f"wrote: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(artifact.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
