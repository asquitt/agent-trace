"""Ranking model - stores AI-generated SWOT analysis and scores."""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .idea import Idea
    from .trace import AITrace


class Ranking(Base):
    """Stores the AI-generated ranking and analysis for an idea."""

    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Link to idea
    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # SWOT Analysis
    strengths: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    weaknesses: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    opportunities: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    threats: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))

    # 6-Dimension Scores (0-100 scale)
    solo_buildable_score: Mapped[float] = mapped_column(Float, nullable=False)
    resource_intensity_score: Mapped[float] = mapped_column(Float, nullable=False)
    moat_potential_score: Mapped[float] = mapped_column(Float, nullable=False)
    market_timing_score: Mapped[float] = mapped_column(Float, nullable=False)
    profitability_path_score: Mapped[float] = mapped_column(Float, nullable=False)
    personal_fit_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Weighted overall score
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Recommendation
    recommendation: Mapped[Optional[str]] = mapped_column(Text)
    action_items: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))

    # Model metadata
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(100))
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Versioning for re-rankings
    version: Mapped[int] = mapped_column(Integer, default=1)

    # Token usage for this ranking
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Relationships
    idea: Mapped["Idea"] = relationship("Idea", back_populates="ranking")
    trace: Mapped[Optional["AITrace"]] = relationship(
        "AITrace",
        back_populates="ranking",
        uselist=False,
    )

    __table_args__ = (Index("ix_rankings_overall_score_desc", overall_score.desc()),)

    def __repr__(self) -> str:
        return f"<Ranking(id={self.id}, idea_id={self.idea_id}, score={self.overall_score:.1f})>"
