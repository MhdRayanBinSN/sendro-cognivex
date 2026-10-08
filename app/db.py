"""SQLModel records and database session helpers."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, JSON, Text, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine

from app.config import get_settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Category(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    canonical_domain: str = Field(index=True, unique=True)
    homepage_url: str
    description: str = ""
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_researched_at: datetime | None = None


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    cost_usd: float = 0.0


class StageLog(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("run_id", "stage", name="uq_stage_run"),)
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    stage: str = Field(index=True)
    status: str = "pending"
    input_hash: str | None = None
    output_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    duration_seconds: float | None = None
    attempts: int = 0
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    updated_at: datetime = Field(default_factory=utc_now)


class Page(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("product_id", "url", name="uq_product_page"),)
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    url: str
    fetch_method: str | None = None
    status_code: int | None = None
    text_hash: str | None = None
    cleaned_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    fetched_at: datetime | None = None
    chosen_reason: str | None = None
    screenshot_flag: bool = False


class Fact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    page_id: int | None = Field(default=None, foreign_key="page.id")
    field: str = Field(index=True)
    value_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    source_url: str
    evidence_quote: str = Field(sa_type=Text)
    kind: str = "fact"
    verified: bool = False


class Screenshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id", index=True)
    path: str
    width: int
    height: int
    caption: str = ""
    quality_score: float | None = None
    accepted: bool = False


class Comparison(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id", index=True)
    product_a_id: int = Field(foreign_key="product.id")
    product_b_id: int = Field(foreign_key="product.id")
    markdown: str = Field(sa_type=Text)
    html: str = Field(sa_type=Text)
    confidence_notes: str = Field(default="", sa_type=Text)
    created_at: datetime = Field(default_factory=utc_now)


class LLMCall(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = Field(default=None, foreign_key="run.id", index=True)
    stage: str = Field(index=True)
    prompt_hash: str = Field(index=True)
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    reasoning: str = Field(default="", sa_type=Text)
    created_at: datetime = Field(default_factory=utc_now)


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        db_path = Path(url.removeprefix("sqlite:///"))
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine()


def create_db_and_tables() -> None:
    """Create missing tables; migrations can replace this during deployment."""
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
