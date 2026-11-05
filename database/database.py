"""PostgreSQL database layer for research history.

Tables:
    research_sessions  — one row per research run
    research_reports   — the final report for each session
    research_sources   — all URLs discovered during research

Works with both PostgreSQL (production) and SQLite (dev fallback)
via the DATABASE_URL in ResearchConfig.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    distinct,
    func,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    joinedload,
    relationship,
    selectinload,
    sessionmaker,
)

from config.config import ResearchConfig
from schemas.models import ResearchPlan, ResearchReport

logger = logging.getLogger(__name__)


# --- Metric Status Normalization ---
# Legacy rows (pre Phase 2) stored "complete"/"error"; new rows store
# "SUCCESS"/"FAILED". Analytics queries map both vocabularies.
SUCCESS_STATUSES = ("SUCCESS", "complete")
FAILED_STATUSES = ("FAILED", "error", "complete_unstructured")

# Max length of a persisted error message (failed runs only).
_ERROR_MESSAGE_MAX_LEN = 512

# Matches credentials embedded in connection URLs (e.g. inside exception text).
_CREDENTIAL_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


# --- SQLAlchemy Models ---

class Base(DeclarativeBase):
    pass


class ResearchSession(Base):
    """One row per research run."""

    __tablename__ = "research_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(Text, nullable=False)
    search_queries = Column(JSON, default=list)
    focus_areas = Column(JSON, default=list)
    facts = Column(JSON, default=list)
    status = Column(String(50), default="started")
    model_name = Column(String(100), nullable=True)
    execution_time_seconds = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    run_id = Column(String(36), nullable=True, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    report = relationship(
        "ResearchReportRow",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    sources = relationship(
        "ResearchSource",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ResearchSession id={self.id} topic='{self.topic[:40]}...'>"


class ResearchReportRow(Base):
    """The final report for a research session."""

    __tablename__ = "research_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("research_sessions.id"), nullable=False, unique=True)
    title = Column(Text, nullable=False)
    outline = Column(JSON, default=list)
    report_content = Column(Text, nullable=False)
    sources = Column(JSON, default=list)
    word_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship
    session = relationship("ResearchSession", back_populates="report")

    def __repr__(self) -> str:
        return f"<ResearchReportRow id={self.id} title='{self.title[:40]}...'>"


class ResearchSource(Base):
    """A URL discovered during research."""

    __tablename__ = "research_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("research_sessions.id"), nullable=False)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    snippet = Column(Text, nullable=True)
    query = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship
    session = relationship("ResearchSession", back_populates="sources")

    def __repr__(self) -> str:
        return f"<ResearchSource id={self.id} url='{self.url[:50]}...'>"


# --- Engine & Session Factory ---

_engine = None
_SessionFactory = None


def get_engine(config: ResearchConfig):
    """Get or create the SQLAlchemy engine (singleton).

    Raises:
        RuntimeError: If no database URL is configured. The DSN comes from
            the DATABASE_URL environment variable (typically a git-ignored
            .env file), so this fails fast with setup guidance instead of
            trying to connect to an unset target.
    """
    global _engine
    if _engine is None:
        if not config.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Copy .env.example to .env "
                "and set DATABASE_URL to your PostgreSQL connection string "
                "(postgresql://<user>:<password>@<host>:<port>/<database>)."
            )
        _engine = create_engine(
            config.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        logger.info(f"Database engine created: {config.database_url.split('@')[-1] if '@' in config.database_url else config.database_url}")
    return _engine


def get_session(config: ResearchConfig) -> Session:
    """Get a new database session."""
    global _SessionFactory
    if _SessionFactory is None:
        engine = get_engine(config)
        _SessionFactory = sessionmaker(bind=engine)
    return _SessionFactory()


def init_database(config: ResearchConfig) -> None:
    """Create all tables if they don't exist.

    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
    """
    engine = get_engine(config)
    Base.metadata.create_all(engine)
    _migrate_add_columns(engine)
    logger.info("Database tables initialized")


def _migrate_add_columns(engine) -> None:
    """Idempotent lightweight migration for columns added after initial release.

    ``create_all`` only creates missing tables; it never ALTERs existing ones.
    This inspects the live schema and adds any missing columns so existing
    deployments (with historic research_sessions rows) pick up new fields.

    Never drops or recreates tables and never touches existing rows —
    all added columns are nullable, so historic records keep their values.
    """
    # Column additions keyed by (name, DDL type) — safe for PostgreSQL + SQLite.
    new_columns = {
        "execution_time_seconds": "FLOAT",
        "error_message": "TEXT",
        "run_id": "VARCHAR(36)",
    }
    try:
        existing = {c["name"] for c in inspect(engine).get_columns("research_sessions")}
        with engine.begin() as conn:
            for col_name, col_type in new_columns.items():
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE research_sessions ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"Migration: added research_sessions.{col_name}")
            # Unique index for the idempotency key. Multiple NULLs are allowed
            # (legacy rows), and re-creating an existing index is a no-op.
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_research_sessions_run_id "
                "ON research_sessions (run_id)"
            ))
    except Exception as e:
        # Never let a migration check break app startup — the columns are
        # nullable, so reads/writes remain safe either way.
        logger.warning(f"Schema migration check skipped: {e}")


# --- CRUD Operations ---

def _sanitize_error_message(message: Optional[str], config: ResearchConfig) -> Optional[str]:
    """Make an error message safe to persist.

    - Masks ``scheme://user:pass@`` credential blocks (exceptions from
      SQLAlchemy/psycopg2 often embed the full DSN).
    - Replaces the configured database URL verbatim if it appears.
    - Truncates to ``_ERROR_MESSAGE_MAX_LEN`` characters.
    """
    if not message:
        return None
    clean = _CREDENTIAL_RE.sub("://***:***@", message)
    if config.database_url:
        clean = clean.replace(config.database_url, "<database-url>")
    clean = clean.strip()
    if len(clean) > _ERROR_MESSAGE_MAX_LEN:
        clean = clean[:_ERROR_MESSAGE_MAX_LEN] + "..."
    return clean


def save_session(
    topic: str,
    plan: Optional[ResearchPlan],
    facts: List[Dict],
    sources: List[str],
    report: Optional[Union[ResearchReport, str]],
    status: str,
    config: ResearchConfig,
    execution_time: Optional[float] = None,
    run_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> int:
    """Save a complete research session to the database.

    Args:
        topic: The research topic.
        plan: The research plan (or None).
        facts: List of research fact dicts.
        sources: List of source URLs.
        report: The final report (ResearchReport or raw string).
        status: Pipeline status string.
        config: App configuration.
        execution_time: Total workflow duration in seconds (optional).
        run_id: Unique idempotency key for one workflow execution.
            If a session with this run_id already exists, no new row is
            created and the existing session ID is returned.
        error_message: Failure reason for FAILED runs (sanitized before
            storage; never include credentials).

    Returns:
        The session ID.
    """
    session = get_session(config)

    try:
        # Idempotency guard: one execution must produce exactly one record.
        if run_id:
            existing = (
                session.query(ResearchSession).filter_by(run_id=run_id).first()
            )
            if existing:
                logger.warning(
                    f"Duplicate run ignored: run_id={run_id} already saved "
                    f"as session id={existing.id}"
                )
                return existing.id

        # Create session row
        db_session = ResearchSession(
            topic=topic,
            search_queries=plan.search_queries if plan else [],
            focus_areas=plan.focus_areas if plan else [],
            facts=facts,
            status=status,
            model_name=config.llm_model,
            execution_time_seconds=round(execution_time, 2) if execution_time is not None else None,
            error_message=_sanitize_error_message(error_message, config),
            run_id=run_id,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(db_session)
        session.flush()  # Get the ID

        # Save report
        if isinstance(report, ResearchReport):
            db_report = ResearchReportRow(
                session_id=db_session.id,
                title=report.title,
                outline=report.outline,
                report_content=report.report,
                sources=report.sources,
                word_count=report.word_count,
            )
            session.add(db_report)
        elif report:
            db_report = ResearchReportRow(
                session_id=db_session.id,
                title=topic[:200],
                outline=[],
                report_content=str(report),
                sources=sources,
                word_count=len(str(report).split()),
            )
            session.add(db_report)

        # Save sources — deduplicated by URL across queries, so a URL
        # found by multiple search queries is only recorded once.
        recorded_urls: set = set()
        for fact in facts:
            query_text = fact.get("query", "")
            for src in fact.get("sources", []):
                url = src.get("url", "")
                if not url or url in recorded_urls:
                    continue
                recorded_urls.add(url)
                db_source = ResearchSource(
                    session_id=db_session.id,
                    url=url,
                    title=src.get("title", ""),
                    snippet=src.get("snippet", ""),
                    query=query_text,
                )
                session.add(db_source)

        session.commit()
        logger.info(f"Session saved: id={db_session.id}, topic='{topic[:50]}'")
        return db_session.id

    except IntegrityError as e_integrity:
        session.rollback()
        # Unique-violation on run_id means a concurrent save of the same
        # execution won the race — return its row instead of duplicating.
        if run_id:
            existing = (
                session.query(ResearchSession).filter_by(run_id=run_id).first()
            )
            if existing:
                logger.warning(
                    f"Duplicate run resolved via unique constraint: "
                    f"run_id={run_id} -> session id={existing.id}"
                )
                return existing.id
        logger.error(f"Integrity error saving session (run_id={run_id}): {e_integrity}")
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Error saving session: {e}")
        raise
    finally:
        session.close()


def get_all_sessions(config: ResearchConfig) -> List[Dict]:
    """Get all research sessions (most recent first).

    Returns:
        List of dicts with session metadata.
    """
    session = get_session(config)
    try:
        rows = (
            session.query(ResearchSession)
            .order_by(ResearchSession.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "topic": r.topic,
                "status": r.status,
                "model_name": r.model_name,
                "execution_time_seconds": r.execution_time_seconds,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "has_report": r.report is not None,
                # Unique URL count — legacy rows may contain the same URL
                # recorded under multiple queries.
                "source_count": len({s.url for s in r.sources}),
            }
            for r in rows
        ]
    finally:
        session.close()


def get_session_by_id(session_id: int, config: ResearchConfig) -> Optional[Dict]:
    """Get a single research session with its report and sources.

    Args:
        session_id: The session ID to retrieve.
        config: App configuration.

    Returns:
        A dict with full session data, or None if not found.
    """
    session = get_session(config)
    try:
        row = session.query(ResearchSession).filter_by(id=session_id).first()
        if not row:
            return None

        result = {
            "id": row.id,
            "topic": row.topic,
            "search_queries": row.search_queries,
            "focus_areas": row.focus_areas,
            "facts": row.facts,
            "status": row.status,
            "model_name": row.model_name,
            "execution_time_seconds": row.execution_time_seconds,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "report": None,
            "sources": [],
        }

        if row.report:
            result["report"] = {
                "title": row.report.title,
                "outline": row.report.outline,
                "report_content": row.report.report_content,
                "sources": row.report.sources,
                "word_count": row.report.word_count,
            }

        result["sources"] = [
            {
                "url": s.url,
                "title": s.title,
                "snippet": s.snippet,
                "query": s.query,
            }
            for s in row.sources
        ]

        return result
    finally:
        session.close()


def delete_session(session_id: int, config: ResearchConfig) -> bool:
    """Delete a research session and all related data.

    Args:
        session_id: The session ID to delete.
        config: App configuration.

    Returns:
        True if deleted, False if not found.
    """
    session = get_session(config)
    try:
        row = session.query(ResearchSession).filter_by(id=session_id).first()
        if not row:
            return False
        session.delete(row)
        session.commit()
        logger.info(f"Session deleted: id={session_id}")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting session: {e}")
        raise
    finally:
        session.close()


# --- Metrics Operations ---
# All getters read committed rows only (each opens its own DB session and
# closes it in ``finally``), so metrics never count uncommitted work.

def _to_metrics_record(row: "ResearchSession") -> Dict:
    """Map a ResearchSession ORM row to a flat metrics dict."""
    return {
        "id": row.id,
        "run_id": row.run_id,
        "topic": row.topic,
        "status": row.status,
        "execution_time_seconds": row.execution_time_seconds,
        "source_count": len({s.url for s in row.sources if s.url}),
        "report_id": row.report.id if row.report else None,
        "has_report": row.report is not None,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def get_execution_metrics(config: ResearchConfig) -> List[Dict]:
    """Retrieve one metrics record per research execution (newest first).

    Returns:
        List of dicts with: id, run_id, topic, status, execution_time_seconds,
        source_count (unique URLs), report_id, has_report, error_message,
        created_at, completed_at.
    """
    session = get_session(config)
    try:
        rows = (
            session.query(ResearchSession)
            .options(
                joinedload(ResearchSession.report),
                selectinload(ResearchSession.sources),
            )
            .order_by(ResearchSession.created_at.desc())
            .all()
        )
        return [_to_metrics_record(r) for r in rows]
    finally:
        session.close()


def get_success_count(config: ResearchConfig) -> int:
    """Count executions that completed with a valid report."""
    session = get_session(config)
    try:
        return (
            session.query(func.count(ResearchSession.id))
            .filter(ResearchSession.status.in_(SUCCESS_STATUSES))
            .scalar()
            or 0
        )
    finally:
        session.close()


def get_failed_count(config: ResearchConfig) -> int:
    """Count executions that raised an error or produced no valid report."""
    session = get_session(config)
    try:
        return (
            session.query(func.count(ResearchSession.id))
            .filter(ResearchSession.status.in_(FAILED_STATUSES))
            .scalar()
            or 0
        )
    finally:
        session.close()


def get_average_execution_time(config: ResearchConfig) -> Optional[float]:
    """Average execution time in seconds across runs that recorded timing.

    Returns:
        Mean duration rounded to 2 decimals, or None if no timed runs exist
        (e.g. all rows pre-date performance tracking).
    """
    session = get_session(config)
    try:
        avg = (
            session.query(func.avg(ResearchSession.execution_time_seconds))
            .filter(ResearchSession.execution_time_seconds.isnot(None))
            .scalar()
        )
        return round(float(avg), 2) if avg is not None else None
    finally:
        session.close()


def get_average_source_count(config: ResearchConfig) -> Optional[float]:
    """Average unique sources per execution across all recorded runs.

    Sessions without any stored source contribute 0. Legacy duplicate-URL
    rows are handled via COUNT(DISTINCT url).

    Returns:
        Mean unique source count rounded to 2 decimals, or None when no
        executions exist.
    """
    session = get_session(config)
    try:
        n_sessions = session.query(func.count(ResearchSession.id)).scalar() or 0
        if n_sessions == 0:
            return None
        per_session = (
            session.query(
                ResearchSource.session_id,
                func.count(distinct(ResearchSource.url)),
            )
            .group_by(ResearchSource.session_id)
            .all()
        )
        total_sources = sum(count for _, count in per_session)
        return round(total_sources / n_sessions, 2)
    finally:
        session.close()


def get_total_reports(config: ResearchConfig) -> int:
    """Total successfully generated and persisted reports.

    Counts committed rows in research_reports. Since Phase 2, report rows
    are only written for SUCCESS runs, so this never counts failed or
    duplicate insertions.
    """
    session = get_session(config)
    try:
        return session.query(func.count(ResearchReportRow.id)).scalar() or 0
    finally:
        session.close()


# --- Analytics Dashboard Operations ---
# Aggregation happens in SQL so the UI never loads whole tables.

def get_total_executions(config: ResearchConfig) -> int:
    """Total recorded research executions (success + failure)."""
    session = get_session(config)
    try:
        return session.query(func.count(ResearchSession.id)).scalar() or 0
    finally:
        session.close()


def get_status_distribution(config: ResearchConfig) -> Dict[str, int]:
    """Success vs failed execution counts, grouped in SQL.

    Legacy status vocabulary is normalized via SUCCESS_STATUSES /
    FAILED_STATUSES so historic rows count correctly.
    """
    session = get_session(config)
    try:
        rows = (
            session.query(
                ResearchSession.status, func.count(ResearchSession.id)
            )
            .group_by(ResearchSession.status)
            .all()
        )
        dist = {"SUCCESS": 0, "FAILED": 0}
        for status, count in rows:
            if status in SUCCESS_STATUSES:
                dist["SUCCESS"] += count
            elif status in FAILED_STATUSES:
                dist["FAILED"] += count
        return dist
    finally:
        session.close()


def get_execution_time_series(
    config: ResearchConfig, limit: int = 100
) -> List[Dict]:
    """Execution durations for the most recent timed runs (newest first).

    Only rows that recorded timing are returned (pre-Phase-2 rows are
    excluded in SQL rather than filtered in Python).
    """
    session = get_session(config)
    try:
        rows = (
            session.query(
                ResearchSession.id,
                ResearchSession.topic,
                ResearchSession.created_at,
                ResearchSession.execution_time_seconds,
            )
            .filter(ResearchSession.execution_time_seconds.isnot(None))
            .order_by(ResearchSession.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "topic": r.topic,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "execution_time_seconds": r.execution_time_seconds,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_daily_activity(config: ResearchConfig, days: int = 90) -> List[Dict]:
    """Execution counts per calendar day over the recent window (SQL GROUP BY)."""
    session = get_session(config)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        day_col = func.date(ResearchSession.created_at)
        rows = (
            session.query(day_col, func.count(ResearchSession.id))
            .filter(ResearchSession.created_at >= cutoff)
            .group_by(day_col)
            .order_by(day_col)
            .all()
        )
        return [{"date": str(d), "count": c} for d, c in rows]
    finally:
        session.close()


def get_source_count_series(
    config: ResearchConfig, limit: int = 50
) -> List[Dict]:
    """Unique source count per execution for recent runs (newest first).

    Uses COUNT(DISTINCT url) with an OUTER JOIN in SQL, so runs with zero
    sources still appear and legacy duplicate rows collapse correctly.
    """
    session = get_session(config)
    try:
        rows = (
            session.query(
                ResearchSession.id,
                ResearchSession.topic,
                ResearchSession.created_at,
                func.count(distinct(ResearchSource.url)),
            )
            .outerjoin(ResearchSource, ResearchSource.session_id == ResearchSession.id)
            .group_by(
                ResearchSession.id,
                ResearchSession.topic,
                ResearchSession.created_at,
            )
            .order_by(ResearchSession.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": sid,
                "topic": topic,
                "created_at": created.isoformat() if created else None,
                "source_count": count,
            }
            for sid, topic, created, count in rows
        ]
    finally:
        session.close()


def get_execution_history_records(
    config: ResearchConfig,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    search_text: Optional[str] = None,
    limit: int = 200,
) -> List[Dict]:
    """Filtered execution records for the history table.

    All filters are applied in SQL (never in Python after a full-table
    load). Relations are eager-loaded to avoid N+1 queries.

    Args:
        status: None (all), "SUCCESS" or "FAILED" (legacy vocabulary is
            normalized via the status constants).
        since: Only executions created at/after this datetime (UTC).
        search_text: Case-insensitive substring match on the research topic.
        limit: Max rows returned (guard against loading unnecessary data).
    """
    session = get_session(config)
    try:
        query = (
            session.query(ResearchSession)
            .options(
                joinedload(ResearchSession.report),
                selectinload(ResearchSession.sources),
            )
        )
        if status == "SUCCESS":
            query = query.filter(ResearchSession.status.in_(SUCCESS_STATUSES))
        elif status == "FAILED":
            query = query.filter(ResearchSession.status.in_(FAILED_STATUSES))
        if since is not None:
            query = query.filter(ResearchSession.created_at >= since)
        if search_text:
            query = query.filter(
                ResearchSession.topic.ilike(f"%{search_text.strip()}%")
            )
        rows = (
            query.order_by(ResearchSession.created_at.desc()).limit(limit).all()
        )
        return [_to_metrics_record(r) for r in rows]
    finally:
        session.close()
