"""Run context. Application state, not model-authored text."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schemas import ContextVerdict, WorkOrder


@dataclass
class DispatchContext:
    """Carried through a run. The worker never writes to this; the SDK does.

    ``work_order`` is set by the director's ``on_handoff`` callback from the
    validated payload, and read by the handoff input filter to build the
    worker's entire input. Nothing else the director said travels.

    ``verdict`` and ``override`` are phase 2, 4 September 2026, and neither is
    model-authored. Both are stamped by the launcher before the run starts,
    which is the whole point of them living here rather than on ``WorkOrder``.

    The scope proposed a required ``context: ContextVerdict`` field on the work
    order, reasoning that "the Director cannot construct a passing verdict
    itself because it holds no CRM tool". That is a capability argument about
    obtaining evidence, and the gap is that typing JSON needs no tool: a
    Director with nothing but a keyboard can write ``status="net_new"`` and a
    plausible ``searched`` line, and a required field would have made that
    mandatory rather than impossible. So the verdict is carried here instead,
    where no agent can write it, and the handoff reads it from the run rather
    than from the model's payload.
    """

    operator: str = "Kib Cochran"
    work_order: WorkOrder | None = None
    dispatched_to: str | None = None
    dispatched_at: str | None = None
    handoff_count: int = 0
    apollo_reveals: int = 0
    audit: list[str] = field(default_factory=list)

    # Phase 2. Set by the launcher from --verdict, never by an agent.
    verdict: ContextVerdict | None = None
    # Kib's reason for proceeding anyway, from --override. An empty override is
    # no override: the flag requires a reason so the ledger records why.
    override: str | None = None

    def stamp_context(
        self, verdict: ContextVerdict | None, override: str | None = None
    ) -> None:
        """Called once by the launcher, before the run."""
        self.verdict = verdict
        self.override = (override or "").strip() or None
        if verdict is not None:
            self.audit.append(
                f"context verdict: {verdict.status} for "
                f"{verdict.person or verdict.account}"
            )
        if self.override:
            self.audit.append(f"override: {self.override}")

    def record_dispatch(self, to_agent: str, order: WorkOrder) -> None:
        self.work_order = order
        self.dispatched_to = to_agent
        self.dispatched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.handoff_count += 1
        self.audit.append(
            f"{self.dispatched_at} handoff -> {to_agent}: {order.model_dump_json()}"
        )
