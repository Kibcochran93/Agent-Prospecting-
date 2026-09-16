"""When a page view is allowed to cost money.

Kib's decision, 10 September: enrich on discovery, but only for high-intent
paths. A visit to /demo or /pricing may trigger a reveal; a blog post may not.

The point is that a reveal costs a credit and happens before anyone has decided
anything about the account. So the gate is deliberately narrow and deliberately
dumb: it permits a spend only when the viewed path is listed in the tracker
config at level high, and refuses in every other case, including the cases it
does not recognise.

Four refusals, all of them structural rather than advisory:

Unknown path, no reveal.
    A path absent from the config is not "probably fine". Apollo's path list is
    editable in a browser, so a path this code has never heard of means the
    config is out of date, and a stale config must not authorise spending.

Stale config, no reveal at all.
    The config carries the date it was read from Apollo. Past the limit the gate
    stops permitting anything, because levels may have been reconfigured since.

Medium and low are not high.
    There is no "high enough". Only high.

The budget is checked before the spend, not after.
    Same failure as 4 September, when six concurrent reveals each read the count
    before any of them incremented it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

#: Past this, the path levels may have been changed in Apollo without this file
#: knowing, so the gate stops authorising spend.
CONFIG_STALE_AFTER_DAYS = 30

#: Reveals permitted per day for discovery. Separate from the per-job cap in
#: job_queue: that one bounds a run Kib asked for, this one bounds spending on
#: strangers.
DISCOVERY_REVEAL_CAP_PER_DAY = int(
    os.environ.get("SEATS_DISCOVERY_REVEAL_CAP", "5")
)

HIGH = "high"


class RevealRefused(Exception):
    """Raised instead of spending a credit. The message is what a person reads."""


@dataclass(frozen=True)
class PathLevel:
    path: str
    label: str
    level: str


def config_path() -> Path:
    return Path(
        os.environ.get("SEATS_INTENT_PATHS", "./config/apollo-intent-paths.json")
    ).resolve()


def load_config(path: Path | None = None) -> tuple[list[PathLevel], date]:
    """The tracker's path levels and the date they were read from Apollo."""
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevealRefused(
            f"The intent path config at {path} could not be read ({exc}), so no "
            "reveal is authorised. Nothing was spent."
        ) from exc
    try:
        read_at = date.fromisoformat(str(raw["read_at"])[:10])
    except (KeyError, ValueError) as exc:
        raise RevealRefused(
            "The intent path config carries no readable read_at date, so its age "
            "cannot be checked and no reveal is authorised."
        ) from exc
    levels = [
        PathLevel(
            path=str(entry.get("path") or ""),
            label=str(entry.get("label") or ""),
            level=str(entry.get("level") or "").lower(),
        )
        for entry in raw.get("paths") or []
    ]
    if not levels:
        raise RevealRefused(
            "The intent path config lists no paths, so nothing is high intent and "
            "no reveal is authorised."
        )
    return levels, read_at


def config_age(read_at: date, today: date | None = None) -> int:
    return ((today or datetime.now(timezone.utc).date()) - read_at).days


def high_intent_paths(path: Path | None = None, today: date | None = None) -> list[str]:
    """The paths Apollo has configured as high intent, or a refusal.

    Refuses on a stale config for the same reason may_reveal does, and this was
    a real hole: until 10 September the staleness check lived only in
    may_reveal, so a caller asking for the path list got a happy answer from a
    config that was months out of date. Every caller of this function is making
    a decision that leads to spending, so the check belongs here too.
    """
    levels, read_at = load_config(path)
    age = config_age(read_at, today)
    if age > CONFIG_STALE_AFTER_DAYS:
        raise RevealRefused(
            f"The intent path config was read from Apollo {age} days ago, past "
            f"the {CONFIG_STALE_AFTER_DAYS} day limit. Path levels are editable "
            "in Apollo, so a stale copy is not allowed to define high intent. "
            "Re-read the tracker config."
        )
    return [p.path for p in levels if p.level == HIGH]


def _normalise(viewed: str) -> str:
    text = viewed.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            text = text[text.find("/"):] if "/" in text else "/"
    text = text.split("?")[0].split("#")[0]
    if not text.startswith("/"):
        text = "/" + text
    return text.rstrip("/") or "/"


def match(viewed: str, levels: list[PathLevel]) -> PathLevel | None:
    """The configured path a view corresponds to, or nothing.

    Prefix matching, longest first, so /pricing/enterprise matches /pricing and
    /platform-comparison does not match /platform.
    """
    target = _normalise(viewed)
    for entry in sorted(levels, key=lambda p: len(p.path), reverse=True):
        configured = _normalise(entry.path)
        if target == configured or target.startswith(configured + "/"):
            return entry
    return None


def may_reveal(
    pages_viewed: list[str],
    *,
    spent_today: int = 0,
    today: date | None = None,
    config: Path | None = None,
) -> tuple[bool, str]:
    """Whether a reveal is authorised for this visit, and why or why not.

    Returns rather than raises, because a refusal is the common case and is not
    an error: most page views should not cost anything.
    """
    today = today or datetime.now(timezone.utc).date()
    levels, read_at = load_config(config)

    age = config_age(read_at, today)
    if age > CONFIG_STALE_AFTER_DAYS:
        return False, (
            f"The intent path config was read from Apollo {age} days ago, past the "
            f"{CONFIG_STALE_AFTER_DAYS} day limit. Path levels are editable in "
            "Apollo, so a stale copy is not allowed to authorise spend. Re-read "
            "the tracker config, then try again."
        )

    if spent_today >= DISCOVERY_REVEAL_CAP_PER_DAY:
        return False, (
            f"{spent_today} discovery reveals already today and the cap is "
            f"{DISCOVERY_REVEAL_CAP_PER_DAY}. Nothing was spent."
        )

    if not pages_viewed:
        return False, (
            "No pages recorded for this visit, so there is nothing to judge. A "
            "visit with no path is not high intent by default."
        )

    unknown: list[str] = []
    seen: list[str] = []
    for viewed in pages_viewed:
        hit = match(viewed, levels)
        if hit is None:
            unknown.append(_normalise(viewed))
            continue
        seen.append(f"{_normalise(viewed)} ({hit.level})")
        if hit.level == HIGH:
            return True, (
                f"{_normalise(viewed)} is configured high intent as "
                f"{hit.label!r}. One reveal authorised."
            )

    detail = "; ".join(seen) if seen else "none recognised"
    note = ""
    if unknown:
        note = (
            f" Paths not in the config, treated as not high intent: "
            f"{', '.join(sorted(set(unknown)))}. If they should be, add them in "
            "Apollo and re-read the config."
        )
    return False, (
        f"No high intent path in this visit. Viewed: {detail}.{note} Nothing was "
        "spent."
    )
