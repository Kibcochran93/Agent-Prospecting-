"""The four agents and how they connect.

Topology:

                     Kib
                      |
        +-------------+--------------+
        |                            |
    Director                     Reviewer   (paste only, unreachable by handoff)
        |
   handoff (one per run, typed payload, isolated context)
        |
   +----+-----+
   |          |
Briefing   Campaign
   |          |
   +----+-----+
        |
  handoff back to Director on scope change

Rules the shape enforces, in code rather than in prose:

- No lateral handoff between workers. Neither worker holds a handoff to the
  other, so a briefing cannot set up its own campaign.
- The Reviewer is unreachable. Nothing holds a handoff to it and it holds none.
- One handoff per director run, enforced in ``_dispatch_recorder``.
- The Director never sees the artifact, because handoff transfers ownership of
  the reply and ends its turn.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agents import Agent, ModelSettings
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

from . import connectors, settings
from .context import DispatchContext
from .schemas import ContextVerdict, ReviewVerdict
from .handoff_wiring import scope_change_handoff, work_order_handoff

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def _compose(name: str, *, shared: tuple[str, ...], handoffs: bool) -> str:
    parts = [_read(name), *(_read(s) for s in shared)]
    body = "\n\n".join(parts)
    return f"{RECOMMENDED_PROMPT_PREFIX}\n\n{body}" if handoffs else body


def build_agents() -> dict[str, Agent[DispatchContext]]:
    """Construct the network. Call once per process."""

    briefing: Agent[DispatchContext] = Agent(
        name="Prospect Briefing",
        handoff_description=(
            "One named contact at one US higher education institution. Research, "
            "evidence table, buying group, discovery questions, and one draft."
        ),
        instructions=_compose(
            "briefing",
            shared=(
                "_shared_evidence",
                "_shared_highered",
                "_shared_us_language",
                "_shared_injection",
            ),
            handoffs=True,
        ),
        model=settings.MODEL_RESEARCH,
        tools=connectors.briefing_tools(),
    )

    campaign: Agent[DispatchContext] = Agent(
        name="Campaign Builder",
        handoff_description=(
            "Segments and lists. Trigger research, filter logic, buying group by "
            "institution type, campaign copy, and an inactive Apollo sequence "
            "behind an approval."
        ),
        instructions=_compose(
            "campaign",
            shared=(
                "_shared_evidence",
                "_shared_highered",
                "_shared_us_language",
                "_shared_injection",
                # The writer and the grader read one file. Held apart, they
                # drifted, and four rewrites came out of the gap.
                "_shared_copy_standard",
            ),
            handoffs=True,
        ),
        model=settings.MODEL_RESEARCH,
        tools=connectors.campaign_tools(),
    )

    director: Agent[DispatchContext] = Agent(
        name="Director",
        instructions=_compose(
            "director",
            shared=("_shared_evidence", "_shared_injection"),
            handoffs=True,
        ),
        model=settings.MODEL_PLANNING,
        model_settings=ModelSettings(reasoning={"effort": "high"}),
        tools=connectors.director_tools(),
        handoffs=[work_order_handoff(briefing), work_order_handoff(campaign)],
    )

    # Handoff back, attached after construction to avoid a circular reference.
    # Note what is absent: neither worker gets a handoff to the other, and
    # nothing anywhere gets a handoff to the Reviewer.
    briefing.handoffs = [scope_change_handoff(director)]
    campaign.handoffs = [scope_change_handoff(director)]

    # Account Context, phase 1: read only and advisory.
    #
    # Not in the handoff graph. Nothing dispatches to it and it hands off to
    # nothing, which is the same shape as the Reviewer and for a related reason:
    # an agent wired into the flow before its verdict means anything gives the
    # flow a way to route around it. Phase 2 puts the verdict on `WorkOrder` as
    # a required field, and that is what makes the stop real. Until then Kib
    # runs it, reads it, and decides.
    context: Agent[DispatchContext] = Agent(
        name="Account Context",
        handoff_description=(
            "What we already know about one account or person: HubSpot record, "
            "Apollo sequence membership, prior ledger work. Reads only."
        ),
        instructions=_compose(
            "context",
            shared=("_shared_evidence", "_shared_highered", "_shared_injection"),
            handoffs=False,
        ),
        model=settings.MODEL_PLANNING,
        model_settings=ModelSettings(reasoning={"effort": "high"}),
        tools=connectors.context_tools(),
        handoffs=[],
        output_type=ContextVerdict,
    )

    reviewer: Agent[DispatchContext] = Agent(
        name="Reviewer",
        instructions=_compose(
            "reviewer",
            shared=(
                "_shared_highered",
                "_shared_us_language",
                "_shared_copy_standard",
            ),
            handoffs=False,
        ),
        model=settings.MODEL_REVIEW,
        model_settings=ModelSettings(reasoning={"effort": "high"}),
        tools=connectors.reviewer_tools(),
        handoffs=[],
        # Phase 3, 4 September 2026. The verdict was prose, and prose let it be
        # vague about the one thing that matters: what would have to change for
        # this batch to ship. The fields are the discipline.
        output_type=ReviewVerdict,
    )

    return {
        "context": context,
        "director": director,
        "briefing": briefing,
        "campaign": campaign,
        "reviewer": reviewer,
    }
