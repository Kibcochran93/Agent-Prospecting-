"""The batch differentiation check, computed instead of self-reported.

The Campaign Builder answers five questions about its own batch before
presenting it. On the M3 batch it answered no to all five; the Reviewer,
reading the same text, disagreed on four. A producing agent grading its own
output is the weakest link in the chain, and the questions it is asked are not
matters of judgement. Two messages either open with the same eight words or
they do not.

So the mechanical ones move here, where they are arithmetic over the text:

* the same subject line twice
* the same sentence in two messages
* the same opening
* the same closing move
* a recipient that is a placeholder rather than a person or a named role
* an institution with only one thread

These are ``FAIL``. They are also enforced on the write path: ``SequenceProposal``
runs them, so a batch that repeats itself cannot reach the Apollo gate at all.

Two more are ``REPORT``, because they need Kib's eye rather than a rule:

* a named offer repeated across most of the batch, which is what the two-lane
  template looked like from the outside: every executive got one offer, every
  operational owner got the other
* a date over twelve months old stated without a staleness marker, which is how
  a strategic plan approved in June 2024 was written as current

What stays with the Reviewer: register, whether a detail is genuinely specific,
whether a touch carries new value, whether the motion fits. Judgement, which is
what a reviewer is for. This module exists so no reviewer pass is ever again
spent saying two subject lines are identical.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

FAIL = "FAIL"
REPORT = "REPORT"

_STALE_WINDOW_DAYS = 365

_H3 = re.compile(r"^### +(?P<institution>.+?)\s*$")
# A message ends at the next heading of any level, not only the next institution.
# The last message in a batch has no institution after it, so without this its
# body ran on into whatever section followed. On 4 September that put the
# producing agent's own differentiation check inside the draft that reached
# Apollo, and left the sign-off buried mid-body where the stripper could not
# reach it.
_H2_END = re.compile(r"^## +\S")
# "**To: Provost**" and "**To Rachel Broussard, Coordinator of Events**" are
# both live shapes. A parser that only knows the first reads a real batch as
# zero messages and reports nothing, which is worse than reporting a failure.
_TO = re.compile(r"^\*\*To:?\s+(?P<recipient>.+?)\*\*\s*$")
_SUBJECT = re.compile(r"^\*\*Subject:\*\*\s*(?P<subject>.+?)\s*$")

_MARKUP = re.compile(r"[*_`#>\[\]]")
_NON_WORD = re.compile(r"[^\w\s%]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# A recipient that is not a person and not one named role.
_PLACEHOLDER = re.compile(
    r"(\[|\{\{|\bTBD\b|\bname here\b|\bfirst[_ ]name\b|\bappropriate\b|\brelevant\b)",
    re.IGNORECASE,
)

# Two roles joined by a slash, which is a slot nobody filled:
# "Provost / Vice Chancellor for Academic Affairs". Spaces around the slash are
# what distinguishes it from a real title. "Scheduling/NCAA Coordinator" is one
# person's actual job, and flagging it was a false positive on the first live
# M1 batch.
_SLASHED_ROLES = re.compile(r"\s+/\s+")

_CAP_PHRASE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,3})\b")

_STALENESS_MARKERS = (
    "stale",
    "older",
    "no longer",
    "not current",
    "out of date",
    "superseded",
    "since replaced",
    "flagged",
    "may have moved on",
)

_MONTHS = (
    "january february march april may june july august september october "
    "november december"
).split()
_DATE_PATTERNS = (
    re.compile(r"\b(?P<m>[A-Z][a-z]+)\s+(?P<d>\d{1,2}),\s*(?P<y>\d{4})\b"),
    re.compile(r"\b(?P<d>\d{1,2})\s+(?P<m>[A-Z][a-z]+)\s+(?P<y>\d{4})\b"),
    re.compile(r"\b(?P<y>\d{4})-(?P<mn>\d{2})-(?P<dn>\d{2})\b"),
)


# A sign-off is identical across a batch by design. Reading it as the closing
# made five clean messages fail on 4 September: "Kib Cochran / Solutions
# Engineer / SEAtS Software" is six words flattened, so a word-count floor did
# not catch it. The shape is what identifies it: a run of short trailing lines
# with no sentence punctuation.
_SIGNATURE_MAX_LINES = 5
_SIGNATURE_MAX_WORDS = 6


def strip_signature(body: str) -> str:
    """Drop the trailing sign-off block, keeping the message."""
    lines = body.rstrip().splitlines()
    while lines and len(lines) > 1:
        last = lines[-1].strip().rstrip("*_ ")
        if not last:
            lines.pop()
            continue
        words = last.split()
        looks_like_signature = (
            len(words) <= _SIGNATURE_MAX_WORDS
            and not last.endswith((".", "?", "!", ":"))
        )
        if not looks_like_signature:
            break
        lines.pop()
        if len(body.splitlines()) - len(lines) >= _SIGNATURE_MAX_LINES:
            break
    return "\n".join(lines).rstrip()


@dataclass(frozen=True)
class Message:
    institution: str
    recipient: str
    subject: str
    body: str


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.rule}: {self.detail}"


def normalize(text: str) -> str:
    text = _MARKUP.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.lower().split())


def _sentences(body: str) -> list[str]:
    flat = " ".join(_MARKUP.sub(" ", strip_signature(body)).split())
    return [s.strip() for s in _SENTENCE_SPLIT.split(flat) if s.strip()]


def parse_messages(artifact: str) -> list[Message]:
    """Read message blocks out of a copy batch.

    The shape the Campaign Builder writes: an H3 per institution, then one or
    more blocks of ``**To:**`` / ``**Subject:**`` / body.
    """
    messages: list[Message] = []
    institution = ""
    recipient = subject = ""
    body: list[str] = []
    open_block = False

    def flush() -> None:
        nonlocal open_block, recipient, subject, body
        if open_block:
            messages.append(
                Message(institution, recipient, subject, "\n".join(body).strip())
            )
        open_block = False
        recipient = subject = ""
        body = []

    for line in artifact.splitlines():
        if _H2_END.match(line):
            flush()
            institution = ""
            continue
        heading = _H3.match(line)
        if heading:
            flush()
            institution = heading.group("institution")
            continue
        to = _TO.match(line)
        if to:
            flush()
            recipient = to.group("recipient")
            open_block = True
            continue
        subj = _SUBJECT.match(line)
        if subj and open_block:
            subject = subj.group("subject")
            continue
        if open_block and line.strip() not in ("---", ""):
            body.append(line)
        elif open_block:
            body.append("")
    flush()
    return messages


def institution_of(message: Message) -> str:
    """The institution, from a heading that may carry more than one.

    A copy batch heads each section with the institution. A cadence heads each
    touch with institution, person, touch number and day, so the raw heading is
    unique per message and every institution looks like it has one recipient.
    """
    return message.institution.split(",")[0].strip()


def _label(message: Message) -> str:
    who = message.recipient or "unnamed recipient"
    where = message.institution or "unattributed"
    return f"{where} / {who}"


def _pairs(messages: list[Message]):
    for i, left in enumerate(messages):
        for right in messages[i + 1 :]:
            yield left, right


def _opening(message: Message, words: int = 8) -> str:
    return " ".join(normalize(strip_signature(message.body)).split()[:words])


def _closing(message: Message, words: int = 6, floor: int = 5) -> str:
    """The last real sentence, not the sign-off.

    Every message in this system ends "Kib". Reading that as the closing made
    three pairs in the first live M1 batch look like they closed identically,
    which is a false positive that would have blocked a write. So the closing is
    the last sentence with something in it.
    """
    for sentence in reversed(_sentences(message.body)):
        normalized = normalize(sentence).split()
        if len(normalized) >= floor:
            return " ".join(normalized[:words])
    return ""


def _parse_date(text: str) -> date | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        parts = match.groupdict()
        try:
            if parts.get("mn"):
                return date(int(parts["y"]), int(parts["mn"]), int(parts["dn"]))
            month = parts["m"].lower()
            if month not in _MONTHS:
                continue
            return date(int(parts["y"]), _MONTHS.index(month) + 1, int(parts["d"]))
        except ValueError:
            continue
    return None


def hard_checks(messages: list[Message]) -> list[Finding]:
    """The rules that block a write. Every one is a string comparison."""
    findings: list[Finding] = []

    subjects: dict[str, str] = {}
    for message in messages:
        key = normalize(message.subject)
        if not key:
            continue
        if key in subjects:
            findings.append(
                Finding(FAIL, "duplicate-subject", f"{subjects[key]} and {_label(message)}: {message.subject!r}")
            )
        else:
            subjects[key] = _label(message)

    seen_sentences: dict[str, str] = {}
    for message in messages:
        for sentence in _sentences(message.body):
            key = normalize(sentence)
            if len(key.split()) < 6:
                continue
            if key in seen_sentences and seen_sentences[key] != _label(message):
                findings.append(
                    Finding(
                        FAIL,
                        "duplicate-sentence",
                        f"{seen_sentences[key]} and {_label(message)} share: {sentence[:90]!r}",
                    )
                )
            seen_sentences.setdefault(key, _label(message))

    for left, right in _pairs(messages):
        if _opening(left) and _opening(left) == _opening(right):
            findings.append(
                Finding(
                    FAIL,
                    "shared-opening",
                    f"{_label(left)} and {_label(right)} open on the same eight words",
                )
            )
        if _closing(left) and _closing(left) == _closing(right):
            findings.append(
                Finding(
                    FAIL,
                    "shared-closing",
                    f"{_label(left)} and {_label(right)} close the same way",
                )
            )

    for message in messages:
        if _PLACEHOLDER.search(message.recipient) or _SLASHED_ROLES.search(
            message.recipient
        ):
            findings.append(
                Finding(
                    FAIL,
                    "placeholder-recipient",
                    f"{_label(message)}: {message.recipient!r} is a slot, not a person or one named role",
                )
            )

    # Single threading is a question about a batch of first touches. A cadence
    # is not single threaded, it is a sequence: the same person, several times,
    # on purpose. Asked of a follow-up batch the rule fires on every message,
    # which is what it did to twelve clean touches on 4 September.
    seen = Counter(normalize(m.recipient) for m in messages)
    if not any(count > 1 for count in seen.values()):
        threads: dict[str, set[str]] = defaultdict(set)
        for message in messages:
            if message.institution:
                threads[institution_of(message)].add(normalize(message.recipient))
        for institution, recipients in threads.items():
            if len(recipients) < 2:
                findings.append(
                    Finding(FAIL, "single-threaded", f"{institution}: one role only")
                )

    return findings


def soft_checks(messages: list[Message], as_of: date | None = None) -> list[Finding]:
    """The rules that need Kib rather than a refusal."""
    as_of = as_of or date.today()
    findings: list[Finding] = []
    if not messages:
        return findings

    phrase_messages: Counter[str] = Counter()
    phrase_institutions: defaultdict[str, set[str]] = defaultdict(set)
    for message in messages:
        text = f"{message.subject}\n{strip_signature(message.body)}"
        institution_words = set(normalize(message.institution).split())
        for phrase in {p for p in _CAP_PHRASE.findall(text)}:
            if set(normalize(phrase).split()) & institution_words:
                continue
            phrase_messages[phrase] += 1
            phrase_institutions[phrase].add(message.institution)
    for phrase, count in phrase_messages.most_common():
        if count >= max(2, len(messages) // 2) and len(phrase_institutions[phrase]) >= 2:
            findings.append(
                Finding(
                    REPORT,
                    "repeated-offer",
                    f"{phrase!r} appears in {count} of {len(messages)} messages across "
                    f"{len(phrase_institutions[phrase])} institutions",
                )
            )

    for message in messages:
        for sentence in _sentences(f"{message.subject}. {message.body}"):
            found = _parse_date(sentence)
            if not found:
                continue
            age = (as_of - found).days
            if age <= _STALE_WINDOW_DAYS:
                continue
            lowered = sentence.lower()
            if any(marker in lowered for marker in _STALENESS_MARKERS):
                continue
            findings.append(
                Finding(
                    REPORT,
                    "stale-date-stated-as-current",
                    f"{_label(message)}: {found.isoformat()} is {age} days old and carries "
                    f"no staleness marker: {sentence[:90]!r}",
                )
            )

    return findings


# The five questions, and which computed rules answer them. Questions 3 and 5
# are judgement, so nothing maps to them and nothing here pretends otherwise.
_QUESTION_RULES: dict[int, tuple[str, ...]] = {
    1: ("shared-opening", "duplicate-sentence"),
    2: ("shared-closing", "duplicate-sentence"),
    4: ("duplicate-subject", "repeated-offer"),
}

_SELF_ANSWER = re.compile(r"^\s*(?P<n>[1-5])\.\s*(?P<text>.+?)\s*$")


def parse_self_report(artifact: str) -> dict[int, str]:
    """The producing agent's own five answers, as it wrote them."""
    answers: dict[int, str] = {}
    for line in artifact.splitlines():
        match = _SELF_ANSWER.match(line)
        if match:
            answers.setdefault(int(match.group("n")), match.group("text"))
    return answers


def _claims_no(answer: str) -> bool:
    """Did the agent answer no to a question where no means nothing is wrong."""
    tail = normalize(answer).split(":")[-1]
    return " no" in f" {tail}" and " yes" not in f" {tail}"


def self_report_conflicts(artifact: str, findings: list[Finding]) -> list[Finding]:
    """Where the agent's own answer disagrees with the arithmetic.

    Kib's rule, 3 September: the self-check stays in the output and stops being
    treated as evidence. This is what that means in practice. The agent keeps
    answering; where an answer is checkable and wrong, the contradiction is
    printed next to it rather than left for a reviewer to notice on pass three.
    """
    answers = parse_self_report(artifact)
    if not answers:
        return []
    failed = {f.rule for f in findings}
    conflicts: list[Finding] = []
    for question, rules in _QUESTION_RULES.items():
        answer = answers.get(question)
        hit = sorted(failed & set(rules))
        if answer and hit and _claims_no(answer):
            conflicts.append(
                Finding(
                    REPORT,
                    "self-report-contradicted",
                    f"question {question}, the agent answered {answer!r}, and the "
                    f"count says {', '.join(hit)}",
                )
            )
    return conflicts


def check(messages: list[Message], as_of: date | None = None) -> list[Finding]:
    return hard_checks(messages) + soft_checks(messages, as_of=as_of)


def check_artifact(artifact: str, as_of: date | None = None) -> list[Finding]:
    return check(parse_messages(artifact), as_of=as_of)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seats-check-batch",
        description=(
            "Run the mechanical half of the batch differentiation check over a "
            "Campaign Builder run log. Exits 1 if anything fails."
        ),
    )
    parser.add_argument("log", help="Path to the run log, or an extracted artifact.")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="Date to age sources against. Defaults to today.",
    )
    args = parser.parse_args(argv)

    from .review_input import ReviewInputError, build

    raw = Path(args.log).read_text(encoding="utf-8", errors="replace")
    try:
        artifact = build(raw).text
    except ReviewInputError:
        # Checking is also useful on a log the extractor refuses; the point here
        # is the copy, not whether the artifact is clean enough to review.
        artifact = raw

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    messages = parse_messages(artifact)
    findings = check(messages, as_of=as_of)
    findings += self_report_conflicts(artifact, findings)

    print(f"{len(messages)} messages parsed")
    for finding in findings:
        print(finding)
    if not findings:
        print("nothing mechanical to report. The Reviewer's half is judgement.")
    return 1 if any(f.severity == FAIL for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
