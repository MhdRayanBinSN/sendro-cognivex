"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.db import Category, Comparison, LLMCall, Run, StageLog, create_db_and_tables, engine, get_session
from app.run_service import run_pipeline


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    with Session(engine) as session:
        if session.exec(select(Category)).first() is None:
            session.add(Category(name=get_settings().category))
            session.commit()
    yield


app = FastAPI(title="Autonomous Product Research", version="0.1.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def dashboard():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/workspace/{page}", include_in_schema=False)
def workspace_page(page: str):
    if page not in {"runs", "reports", "categories", "settings"}:
        raise HTTPException(status_code=404, detail="Workspace page not found")
    return HTMLResponse((STATIC_DIR / f"{page}.html").read_text(encoding="utf-8"))


@app.get("/static/{asset_name}", include_in_schema=False)
def static_asset(asset_name: str):
    assets = {"styles.css": "text/css", "app.js": "text/javascript", "favicon.svg": "image/svg+xml"}
    if asset_name not in assets:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset_path = STATIC_DIR / asset_name
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return Response(asset_path.read_bytes(), media_type=assets[asset_name])


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # Some browsers request this conventional path even when the HTML declares an icon.
    asset_path = STATIC_DIR / "favicon.svg"
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return Response(asset_path.read_bytes(), media_type="image/svg+xml")


@app.get("/screenshots/{asset_path:path}", include_in_schema=False)
def screenshot_asset(asset_path: str):
    root = Path(get_settings().screenshots_dir).resolve()
    target = (root / asset_path).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return Response(target.read_bytes(), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CategoryPatch(BaseModel):
    active: bool


class RunCreate(BaseModel):
    category: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def require_admin(x_admin_api_key: str | None = Header(default=None)) -> None:
    configured = get_settings().admin_api_key
    # Local development can run without admin authentication. Set ADMIN_API_KEY
    # to enable it when the service is exposed beyond the developer's machine.
    if configured is None or not configured.get_secret_value():
        return
    if x_admin_api_key != configured.get_secret_value():
        raise HTTPException(status_code=401, detail="Invalid admin API key")


@app.get("/categories")
def list_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category).order_by(Category.name)).all()


@app.post("/categories", dependencies=[Depends(require_admin)], status_code=201)
def add_category(payload: CategoryCreate, session: Session = Depends(get_session)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Category name must not be blank")
    if session.exec(select(Category).where(Category.name == name)).first():
        raise HTTPException(status_code=409, detail="Category already exists")
    category = Category(name=name)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.patch("/categories/{category_id}", dependencies=[Depends(require_admin)])
def update_category(category_id: int, payload: CategoryPatch, session: Session = Depends(get_session)):
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    category.active = payload.active
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.post("/runs", dependencies=[Depends(require_admin)], status_code=202)
def create_run(payload: RunCreate, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    category_name = (payload.category or get_settings().category).strip()
    if not category_name:
        raise HTTPException(status_code=422, detail="Category must not be blank")
    category = session.exec(select(Category).where(Category.name == category_name)).first()
    if category is not None and not category.active:
        raise HTTPException(status_code=409, detail="This category is paused")
    run = Run(category=category_name)
    session.add(run)
    session.commit()
    session.refresh(run)
    background_tasks.add_task(run_pipeline, run.id)
    return {"run_id": run.id, "status": run.status, "category": run.category}


@app.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    settings = get_settings()
    stages = session.exec(select(StageLog).where(StageLog.run_id == run_id).order_by(StageLog.id)).all()
    decisions = session.exec(select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.created_at)).all()
    report = session.exec(select(Comparison).where(Comparison.run_id == run_id)
                          .order_by(Comparison.created_at.desc())).first()
    return {"run_id": run.id, "status": run.status, "category": run.category,
            "started_at": run.started_at, "finished_at": run.finished_at, "cost_usd": run.cost_usd,
            "error": run.error, "stages": stages,
            "decisions": [{
                "id": item.id, "stage": item.stage, "model": item.model,
                "provider": ("groq" if item.model in {settings.groq_fallback_model,
                                                        settings.groq_json_fallback_model}
                             else settings.llm_provider.lower()),
                "input_tokens": item.input_tokens, "output_tokens": item.output_tokens,
                "cost_usd": item.cost_usd, "reasoning": item.reasoning,
                "created_at": item.created_at,
            } for item in decisions],
            "tools": {
                "search_provider": settings.search_provider,
                "llm_provider": settings.llm_provider,
                "fast_model": settings.llm_model_fast,
                "strong_model": settings.llm_model_strong,
                "vision_model": settings.llm_model_vision,
                "groq_fallback_model": settings.groq_fallback_model
                    if settings.groq_api_key and settings.groq_api_key.get_secret_value() else None,
                "groq_json_fallback_model": settings.groq_json_fallback_model
                    if settings.groq_api_key and settings.groq_api_key.get_secret_value() else None,
                "github_search_enabled": settings.github_search_enabled,
                "pagespeed_audit_enabled": settings.pagespeed_audit_enabled,
                "product_hunt_search_enabled": settings.product_hunt_search_enabled,
            },
            "report_id": report.id if report else None}


def _set_run_state(run_id: int, target: str, session: Session) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if target == "paused" and run.status not in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="Only pending or running runs can be paused")
    if target == "cancelled" and run.status not in {"pending", "running", "paused"}:
        raise HTTPException(status_code=409, detail="This run has already finished")
    if target == "running" and run.status != "paused":
        raise HTTPException(status_code=409, detail="Only paused runs can be resumed")
    run.status = target
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@app.post("/runs/{run_id}/pause", dependencies=[Depends(require_admin)])
def pause_run(run_id: int, session: Session = Depends(get_session)):
    run = _set_run_state(run_id, "paused", session)
    return {"run_id": run.id, "status": run.status}


@app.post("/runs/{run_id}/resume", dependencies=[Depends(require_admin)])
def resume_run(run_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    run = _set_run_state(run_id, "running", session)
    background_tasks.add_task(run_pipeline, run.id)
    return {"run_id": run.id, "status": run.status}


@app.post("/runs/{run_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_run(run_id: int, session: Session = Depends(get_session)):
    run = _set_run_state(run_id, "cancelled", session)
    return {"run_id": run.id, "status": run.status}


@app.post("/runs/{run_id}/retry", dependencies=[Depends(require_admin)], status_code=202)
def retry_run(run_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    source = session.get(Run, run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if source.status not in {"failed", "partial", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed, partial, or cancelled runs can be retried")
    run = Run(category=source.category)
    session.add(run)
    session.commit()
    session.refresh(run)
    background_tasks.add_task(run_pipeline, run.id)
    return {"run_id": run.id, "status": run.status, "category": run.category}


@app.get("/runs")
def list_runs(limit: int = Query(default=30, ge=1, le=100), session: Session = Depends(get_session)):
    runs = session.exec(select(Run).order_by(Run.id.desc()).limit(limit)).all()
    run_ids = [item.id for item in runs]
    failed_stages: dict[int, list[str]] = {}
    if run_ids:
        stage_logs = session.exec(select(StageLog).where(StageLog.run_id.in_(run_ids))).all()
        for stage in stage_logs:
            legacy_selection_failure = (stage.stage == "select" and stage.output_json
                                        and stage.output_json.get("insufficient_candidates"))
            if stage.status == "failed" or legacy_selection_failure:
                failed_stages.setdefault(stage.run_id, []).append(stage.stage)
    return [{"id": item.id, "category": item.category, "status": item.status,
             "started_at": item.started_at, "finished_at": item.finished_at,
             "cost_usd": item.cost_usd, "error": item.error,
             "failed_stages": failed_stages.get(item.id, [])} for item in runs]


@app.get("/reports")
def list_reports(session: Session = Depends(get_session)):
    reports = session.exec(select(Comparison).order_by(Comparison.created_at.desc())).all()
    return [{"id": item.id, "run_id": item.run_id, "product_a_id": item.product_a_id,
             "product_b_id": item.product_b_id, "confidence_notes": item.confidence_notes,
             "created_at": item.created_at} for item in reports]


@app.get("/usage")
def get_usage(session: Session = Depends(get_session)):
    input_tokens, output_tokens = session.exec(select(
        func.coalesce(func.sum(LLMCall.input_tokens), 0),
        func.coalesce(func.sum(LLMCall.output_tokens), 0),
    )).one()
    return {"input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
            "total_tokens": int(input_tokens + output_tokens)}


@app.get("/settings")
def get_public_settings():
    settings = get_settings()
    return {"category": settings.category, "llm_provider": settings.llm_provider,
            "search_provider": settings.search_provider, "fast_model": settings.llm_model_fast,
            "strong_model": settings.llm_model_strong, "vision_model": settings.llm_model_vision,
            "max_run_minutes": settings.max_run_minutes, "max_run_cost_usd": settings.max_run_cost_usd,
            "min_screenshots": settings.min_screenshots, "schedule_cron": settings.schedule_cron,
            "admin_key_enabled": bool(settings.admin_api_key and settings.admin_api_key.get_secret_value())}


@app.get("/reports/{report_id}")
def get_report(report_id: int, format: str = Query(default="html", pattern="^(html|md)$"),
               session: Session = Depends(get_session)):
    report = session.get(Comparison, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if format == "md":
        return PlainTextResponse(report.markdown, media_type="text/markdown; charset=utf-8")
    return HTMLResponse(report.html)
