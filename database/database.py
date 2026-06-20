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
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

from config.config import ResearchConfig
from schemas.models import ResearchPlan, ResearchReport

logger = logging.getLogger(__name__)


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
    """Get or create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
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
    logger.info("Database tables initialized")


# --- CRUD Operations ---

def save_session(
    topic: str,
    plan: Optional[ResearchPlan],
    facts: List[Dict],
    sources: List[str],
    report: Optional[Union[ResearchReport, str]],
    status: str,
    config: ResearchConfig,
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

    Returns:
        The session ID.
    """
    session = get_session(config)

    try:
        # Create session row
        db_session = ResearchSession(
            topic=topic,
            search_queries=plan.search_queries if plan else [],
            focus_areas=plan.focus_areas if plan else [],
            facts=facts,
            status=status,
            model_name=config.llm_model,
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

        # Save sources
        for fact in facts:
            query_text = fact.get("query", "")
            for src in fact.get("sources", []):
                db_source = ResearchSource(
                    session_id=db_session.id,
                    url=src.get("url", ""),
                    title=src.get("title", ""),
                    snippet=src.get("snippet", ""),
                    query=query_text,
                )
                session.add(db_source)

        session.commit()
        logger.info(f"Session saved: id={db_session.id}, topic='{topic[:50]}'")
        return db_session.id

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
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "has_report": r.report is not None,
                "source_count": len(r.sources),
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
