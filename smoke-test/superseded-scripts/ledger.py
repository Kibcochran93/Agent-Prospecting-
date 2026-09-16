"""Write the ledger record batch 1 should have written itself.

The Campaign Builder is told to append a Draft record at the end of a run. Every
checkpoint looked like the middle of one, so five drafts reached Apollo and the
ledger never heard about it. The prompt now says checkpoint 5; this backfills
the record for the batch that already shipped.

Facts only, no copy, same envelope and hash as any agent-written record.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from seats_prospecting.schemas import LedgerRecord  # noqa: E402
from seats_prospecting.tools.notion_ledger import write_outbox_record  # noqa: E402

record = LedgerRecord(
    institution="Batch 1: Tulsa Community College, Creighton University, Wiley University",
    motion="M3 Student Success / Retention",
    segment=(
        "Three US institutions from Kib's saved LinkedIn prospects: a public community "
        "college in Oklahoma, a private university in Nebraska, a private historically "
        "Black university in Texas. Not a discovered segment; a hand-built named list."
    ),
    account_owner="Kib Cochran",
    outreach_decision="Not required",
    source_agent="Campaign Builder",
    verified_through="2026-04-10",
    facts_carried_over=[
        "Tulsa Community College's Academic Success Coaching program earned national recognition; six coaches serve about 2,100 students a year. Source: TCC news, 10 April 2026.",
        "Creighton University became a 2026 FirstGen Forward Network Champion; the First-Flight pilot launched spring 2026 and names Mary Ann Tietjen as a Success Center leader. Source: Creighton news, 14 April 2026.",
        "Wiley University reported 1,137 students for Fall 2026, up 14.5%, with returning students producing 81% of the growth and fall-to-fall retention at 69.2%. Source: Wiley news, 27 August 2026.",
        "Buying groups named from dated sources: Dewayne Dickens and Eunice Tarver at TCC; Mary Ann Tietjen and W. Wayne Young Jr. at Creighton; Shaniqua Adams at Wiley.",
        "Five manual-email drafts were written to Apollo on 4 September 2026 as one paused sequence per recipient, under the name AI Agent Prospecting Ops. Nothing was sent and no contact was enrolled.",
        "Three contacts were dropped before copy: Odessa Mathis (California Career Institute) for unverified clock-hour status, Jennifer Harpham (University of Akron) and Jonathan Summers (Cedar Crest College) for want of a qualifying trigger.",
    ],
    not_verified=[
        "Erinne Weber's current role at Wiley University; the office page names her but carries no publication date, so Wiley is single threaded.",
        "Incumbent student success or retention platform at all three institutions.",
        "Whether any of the three has a current problem with progress visibility, as opposed to a public achievement the copy infers one from.",
        "Whether Creighton's First-Flight pilot has a review date or an owner for its measures.",
        "Budget authority and buying process at all three.",
    ],
    constraints=[
        "Tulsa Community College and Creighton are Kib's own HubSpot accounts; Wiley has no CRM record and is net new.",
        "None of the three has an open opportunity.",
        "The Reviewer returned rewrite twice on this copy, for batch-wide templating. Kib chose to write the drafts anyway and edit them in Apollo.",
        "Copy is drafts only. No contact is enrolled and nothing can send itself.",
    ],
    what_this_suggests=(
        "Batch 2 should draw on problem-shaped triggers such as audit findings, system "
        "replacements or vacancies rather than awards and enrolment wins, which is the "
        "objection the Reviewer reached through two rewrites."
    ),
)

path = write_outbox_record(record)
print("wrote", path)
