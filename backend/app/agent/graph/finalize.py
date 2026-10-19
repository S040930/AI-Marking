"""图终态收尾：只做 run 状态迁移，不重复写业务成绩。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.state import GradingState
from app.domain.grading_run import TERMINAL_RUN_STATUSES, GradingRunStatus
from app.models.grading_run import GradingRun


def build_finalize_node(
    *,
    session_factory: sessionmaker,
    run_id: int,
    claim_token: str | None,
):
    def finalize(state: GradingState) -> dict[str, Any]:
        final = state.get("final") or {
            "status": "failed",
            "error": "图未产出终态",
        }
        status = final.get("status", "failed")
        target = {
            "completed": GradingRunStatus.completed,
            "cancelled": GradingRunStatus.cancelled,
            "failed": GradingRunStatus.failed,
        }.get(status, GradingRunStatus.failed)
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            if run is not None and run.status not in TERMINAL_RUN_STATUSES:
                run_repo.transition_run(
                    db,
                    run,
                    target,
                    claim_token=claim_token,
                    error=final.get("error") if target == GradingRunStatus.failed else None,
                )
        return {"phase": "done"}

    return finalize
