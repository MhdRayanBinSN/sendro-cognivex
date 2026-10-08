"""Persisted, resumable stage execution with run deadlines and cost caps."""

import asyncio
import hashlib
import json
import time
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlmodel import Session, select

from app.config import get_settings
from app.db import LLMCall, Run, StageLog, utc_now

Stage = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger(__name__)


class RunBudgetExceeded(RuntimeError):
    pass


class RunPaused(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


def _json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def execute_stages(run_id: int, stages: list[tuple[str, Stage]], session: Session,
                         *, initial_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute named stages, saving output after each stage so a retry can resume."""
    settings = get_settings()
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"Unknown run id {run_id}")
    if run.status == "cancelled":
        raise RunCancelled("Run was cancelled")
    if run.status == "paused":
        raise RunPaused("Run is paused")
    run.status = "running"
    run.started_at = run.started_at or utc_now()
    session.add(run)
    session.commit()
    deadline = time.monotonic() + settings.max_run_minutes * 60
    state = dict(initial_state or {})
    for stage_index, (stage_name, stage_fn) in enumerate(stages):
        session.refresh(run)
        if run.status == "cancelled":
            raise RunCancelled("Run was cancelled")
        if run.status == "paused":
            raise RunPaused("Run is paused")
        prior = session.exec(select(StageLog).where(StageLog.run_id == run_id,
                                                    StageLog.stage == stage_name)).first()
        if prior and prior.status == "completed" and prior.output_json is not None:
            state[stage_name] = prior.output_json
            continue
        # If an earlier stage is being retried, any previously completed
        # downstream output may have been built from stale input. Invalidate it
        # so the resumed run rebuilds reports from the new stage results.
        for downstream_name, _ in stages[stage_index + 1:]:
            downstream = session.exec(select(StageLog).where(
                StageLog.run_id == run_id, StageLog.stage == downstream_name)).first()
            if downstream and downstream.status == "completed":
                downstream.status = "pending"
                downstream.output_json = None
                downstream.duration_seconds = None
                downstream.error = None
                downstream.updated_at = utc_now()
                session.add(downstream)
        session.commit()
        if time.monotonic() >= deadline:
            run.status = "partial"
            run.error = "Run deadline reached"
            run.finished_at = utc_now()
            session.add(run)
            session.commit()
            raise RunBudgetExceeded("Run deadline reached")
        total_cost = session.exec(select(LLMCall).where(LLMCall.run_id == run_id)).all()
        if sum(call.cost_usd for call in total_cost) >= settings.max_run_cost_usd:
            run.status = "partial"
            run.error = "Run cost budget reached"
            run.finished_at = utc_now()
            session.add(run)
            session.commit()
            raise RunBudgetExceeded("Run cost budget reached")
        if prior is None:
            prior = StageLog(run_id=run_id, stage=stage_name)
            session.add(prior)
        prior.status = "running"
        prior.input_hash = _json_hash(state)
        prior.updated_at = utc_now()
        session.add(prior)
        session.commit()
        started = time.monotonic()
        try:
            for stage_try in range(3):
                prior.attempts += 1
                prior.updated_at = utc_now()
                session.add(prior)
                session.commit()
                try:
                    output = await asyncio.wait_for(
                        stage_fn(state), timeout=max(1.0, deadline - time.monotonic()))
                    break
                except Exception as exc:
                    retryable = bool(getattr(exc, "retryable", False))
                    if not retryable or stage_try == 2:
                        raise
                    delay = 20 * (stage_try + 1)
                    remaining = deadline - time.monotonic()
                    if remaining <= delay:
                        raise RunBudgetExceeded(
                            "Run deadline reached while waiting to retry a provider request") from exc
                    logger.warning(
                        "Retrying pipeline stage after retryable provider error",
                        extra={"run_id": run_id, "stage": stage_name,
                               "stage_attempt": stage_try + 1, "retry_delay_seconds": delay,
                               "provider_status": getattr(exc, "status", None)},
                    )
                    await asyncio.sleep(delay)
            prior.status = "failed" if output.get("_stage_failed") else "completed"
            prior.output_json = output
            prior.duration_seconds = time.monotonic() - started
            prior.error = None
            state[stage_name] = output
        except Exception as exc:
            if isinstance(exc, (RunPaused, RunCancelled)):
                raise
            logger.exception("Pipeline stage failed", extra={"run_id": run_id, "stage": stage_name})
            prior.status = "failed"
            prior.duration_seconds = time.monotonic() - started
            prior.error = f"{type(exc).__name__}: {exc}"
            session.add(prior)
            session.commit()
            run.status = "partial" if stage_name != stages[0][0] else "failed"
            run.error = prior.error
            run.finished_at = utc_now()
            session.add(run)
            session.commit()
            raise
        session.add(prior)
        session.commit()
        session.refresh(run)
        if run.status == "cancelled":
            raise RunCancelled("Run was cancelled")
        if run.status == "paused":
            raise RunPaused("Run is paused")
    run.status = "completed"
    run.finished_at = utc_now()
    run.cost_usd = sum(call.cost_usd for call in session.exec(select(LLMCall).where(LLMCall.run_id == run_id)).all())
    session.add(run)
    session.commit()
    return state
