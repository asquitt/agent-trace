"""Idea model - represents a scraped product/startup idea."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .ranking import Ranking
    from .trace import AITrace


class SourceType(str, Enum):
    """Source where the idea was scraped from."""

    HACKERNEWS = "hackernews"
    INDIEHACKERS = "indiehackers"
    YCOMBINATOR = "ycombinator"
    PRODUCTHUNT = "producthunt"
    MANUAL = "manual"


class IdeaStatus(str, Enum):
    """Processing status of an idea."""

    PENDING = "pending"  # Just scraped, awaiting embedding
    EMBEDDED = "embedded"  # Embedding generated
    RANKED = "ranked"  # Ranking completed
    ARCHIVED = "archived"  # Old or duplicate


class Idea(Base):
    """Represents a product or startup idea from various sources."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Source identification
    source_type: Mapped[SourceType] = mapped_column(nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048))

    # Content
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    tagline: Mapped[Optional[str]] = mapped_column(String(500))
    tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(100)))

    # Metadata from source
    source_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    upvotes: Mapped[Optional[int]] = mapped_column()
    comments_count: Mapped[Optional[int]] = mapped_column()

    # Processing
    status: Mapped[IdeaStatus] = mapped_column(default=IdeaStatus.PENDING, index=True)
    scraped_at: Mapped[datetime] = mapped_column(nullable=False)

    # Vector embedding (1536 dimensions for text-embedding-3-small)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))

    # Duplicate detection
    is_duplicate: Mapped[bool] = mapped_column(default=False)
    duplicate_of_id: Mapped[Optional[int]] = mapped_column()

    # Relationships
    ranking: Mapped[Optional["Ranking"]] = relationship(
        "Ranking",
        back_populates="idea",
        uselist=False,
        cascade="all, delete-orphan",
    )
    traces: Mapped[list["AITrace"]] = relationship(
        "AITrace",
        back_populates="idea",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_ideas_source"),
        Index("ix_ideas_status_scraped", "status", "scraped_at"),
        Index("ix_ideas_source_type_status", "source_type", "status"),
    )

    def __repr__(self) -> str:
        return f"<Idea(id={self.id}, name='{self.name[:30]}...', source={self.source_type.value})>"
