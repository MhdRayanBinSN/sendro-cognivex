import asyncio

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import Run, StageLog
from app.llm import LLMError
from app.orchestrator import RunPaused, execute_stages


def test_completed_stages_are_reused_on_resume():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    calls = {"first": 0, "second": 0}

    async def first(state):
        calls["first"] += 1
        return {"value": 7}

    async def second(state):
        calls["second"] += 1
        return {"seen": state["first"]["value"]}

    with Session(engine) as session:
        run = Run(category="test")
        session.add(run)
        session.commit()
        session.refresh(run)
        stages = [("first", first), ("second", second)]
        first_run = asyncio.run(execute_stages(run.id, stages, session))
        resumed = asyncio.run(execute_stages(run.id, stages, session))
        assert first_run == resumed
        assert calls == {"first": 1, "second": 1}
        assert session.get(Run, run.id).status == "completed"


def test_retried_stage_invalidates_completed_downstream_outputs():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    calls = {"first": 0, "second": 0}

    async def first(state):
        calls["first"] += 1
        return {"value": calls["first"]}

    async def second(state):
        calls["second"] += 1
        return {"seen": state["first"]["value"]}

    with Session(engine) as session:
        run = Run(category="test")
        session.add(run); session.commit(); session.refresh(run)
        stages = [("first", first), ("second", second)]
        asyncio.run(execute_stages(run.id, stages, session))
        first_log = session.exec(select(StageLog).where(
            StageLog.run_id == run.id, StageLog.stage == "first")).one()
        first_log.status = "failed"
        session.add(first_log); session.commit()

        resumed = asyncio.run(execute_stages(run.id, stages, session))
        assert resumed["second"] == {"seen": 2}
        assert calls == {"first": 2, "second": 2}


def test_retryable_llm_stage_error_retries_and_records_attempts(monkeypatch):
    import app.orchestrator as orchestrator

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: type("Settings", (), {
        "max_run_minutes": 2, "max_run_cost_usd": 5})())

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", no_wait)
    calls = 0

    async def flaky_stage(state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMError("provider temporarily unavailable", status=503)
        return {"ok": True}

    with Session(engine) as session:
        run = Run(category="test")
        session.add(run)
        session.commit()
        session.refresh(run)
        result = asyncio.run(execute_stages(run.id, [("flaky", flaky_stage)], session))

        assert result == {"flaky": {"ok": True}}
        stage_log = session.exec(select(StageLog).where(StageLog.run_id == run.id)).one()
        assert stage_log.attempts == 2
        assert stage_log.status == "completed"


def test_paused_run_stops_after_current_stage_and_resumes(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.orchestrator.get_settings", lambda: type("Settings", (), {
        "max_run_minutes": 2, "max_run_cost_usd": 5})())
    calls = {"first": 0, "second": 0}

    async def first(state):
        calls["first"] += 1
        return {"value": 7}

    async def second(state):
        calls["second"] += 1
        return {"seen": state["first"]["value"]}

    with Session(engine) as session:
        run = Run(category="test")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

        async def pause_after_first(state):
            output = await first(state)
            current = session.get(Run, run_id)
            current.status = "paused"
            session.add(current)
            session.commit()
            return output

        try:
            asyncio.run(execute_stages(run_id, [("first", pause_after_first), ("second", second)], session))
        except RunPaused:
            pass

        assert calls == {"first": 1, "second": 0}
        assert session.get(Run, run_id).status == "paused"
        session.get(Run, run_id).status = "running"
        session.commit()
        asyncio.run(execute_stages(run_id, [("first", first), ("second", second)], session))
        assert calls == {"first": 1, "second": 1}
