"""Initial schema with ideas, rankings, and AI traces.

Revision ID: 001
Revises:
Create Date: 2026-01-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable required extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Create enum types using DO block with IF NOT EXISTS logic
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'source_type') THEN
                CREATE TYPE source_type AS ENUM ('hackernews', 'indiehackers', 'ycombinator', 'producthunt', 'manual');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'idea_status') THEN
                CREATE TYPE idea_status AS ENUM ('pending', 'embedded', 'ranked', 'archived');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trace_type') THEN
                CREATE TYPE trace_type AS ENUM ('embedding', 'ranking', 'swot_analysis', 'similarity_search', 'digest_generation', 'recommendation');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trace_status') THEN
                CREATE TYPE trace_status AS ENUM ('running', 'completed', 'failed', 'timeout');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'span_type') THEN
                CREATE TYPE span_type AS ENUM ('llm_call', 'embedding_call', 'vector_search', 'prompt_template', 'response_parse', 'cache_hit', 'retry', 'score_calculation');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'span_status') THEN
                CREATE TYPE span_status AS ENUM ('running', 'completed', 'failed');
            END IF;
        END$$;
    """)

    # Reference the enum types for use in table definitions
    source_type_enum = postgresql.ENUM(
        "hackernews", "indiehackers", "ycombinator", "producthunt", "manual",
        name="source_type", create_type=False
    )
    idea_status_enum = postgresql.ENUM(
        "pending", "embedded", "ranked", "archived",
        name="idea_status", create_type=False
    )
    trace_type_enum = postgresql.ENUM(
        "embedding", "ranking", "swot_analysis", "similarity_search", "digest_generation", "recommendation",
        name="trace_type", create_type=False
    )
    trace_status_enum = postgresql.ENUM(
        "running", "completed", "failed", "timeout",
        name="trace_status", create_type=False
    )
    span_type_enum = postgresql.ENUM(
        "llm_call", "embedding_call", "vector_search", "prompt_template", "response_parse", "cache_hit", "retry", "score_calculation",
        name="span_type", create_type=False
    )
    span_status_enum = postgresql.ENUM(
        "running", "completed", "failed",
        name="span_status", create_type=False
    )

    # Ideas table
    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_type", source_type_enum, nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tagline", sa.String(500), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(100)), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("upvotes", sa.Integer(), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=True),
        sa.Column("status", idea_status_enum, nullable=False, server_default="pending"),
        sa.Column("scraped_at", sa.DateTime(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_ideas_source"),
    )
    op.create_index("ix_ideas_source_type", "ideas", ["source_type"])
    op.create_index("ix_ideas_status", "ideas", ["status"])
    op.create_index("ix_ideas_status_scraped", "ideas", ["status", "scraped_at"])
    op.create_index("ix_ideas_source_type_status", "ideas", ["source_type", "status"])

    # Rankings table
    op.create_table(
        "rankings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("idea_id", sa.Integer(), nullable=False),
        sa.Column("strengths", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("weaknesses", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("opportunities", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("threats", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("solo_buildable_score", sa.Float(), nullable=False),
        sa.Column("resource_intensity_score", sa.Float(), nullable=False),
        sa.Column("moat_potential_score", sa.Float(), nullable=False),
        sa.Column("market_timing_score", sa.Float(), nullable=False),
        sa.Column("profitability_path_score", sa.Float(), nullable=False),
        sa.Column("personal_fit_score", sa.Float(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("action_items", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rankings_idea_id", "rankings", ["idea_id"], unique=True)
    op.create_index(
        "ix_rankings_overall_score_desc",
        "rankings",
        [sa.text("overall_score DESC")],
    )

    # AI Traces table
    op.create_table(
        "ai_traces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("parent_trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_type", trace_type_enum, nullable=False),
        sa.Column(
            "status", trace_status_enum, nullable=False, server_default="running"
        ),
        sa.Column("idea_id", sa.Integer(), nullable=True),
        sa.Column("ranking_id", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(255), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["parent_trace_id"], ["ai_traces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ranking_id"], ["rankings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_traces_parent_trace_id", "ai_traces", ["parent_trace_id"])
    op.create_index("ix_ai_traces_correlation_id", "ai_traces", ["correlation_id"])
    op.create_index("ix_ai_traces_idea_id", "ai_traces", ["idea_id"])
    op.create_index("ix_ai_traces_celery_task_id", "ai_traces", ["celery_task_id"])
    op.create_index("ix_ai_traces_type_status", "ai_traces", ["trace_type", "status"])
    op.create_index("ix_ai_traces_started_at", "ai_traces", ["started_at"])
    op.create_index(
        "ix_ai_traces_correlation_created",
        "ai_traces",
        ["correlation_id", "created_at"],
    )

    # AI Trace Spans table
    op.create_table(
        "ai_trace_spans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_span_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("span_type", span_type_enum, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("assistant_response", sa.Text(), nullable=True),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", span_status_enum, nullable=False, server_default="running"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["trace_id"], ["ai_traces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_span_id"], ["ai_trace_spans.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_trace_spans_trace_id", "ai_trace_spans", ["trace_id"])
    op.create_index(
        "ix_ai_trace_spans_trace_started",
        "ai_trace_spans",
        ["trace_id", "started_at"],
    )
    op.create_index(
        "ix_ai_trace_spans_provider_model",
        "ai_trace_spans",
        ["provider", "model"],
    )

    # AI Trace Reasoning table
    op.create_table(
        "ai_trace_reasoning",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_context", postgresql.JSONB(), nullable=True),
        sa.Column("output_result", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("dimension", sa.String(50), nullable=True),
        sa.Column("raw_score", sa.Float(), nullable=True),
        sa.Column("weighted_score", sa.Float(), nullable=True),
        sa.Column("weight_applied", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["span_id"], ["ai_trace_spans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_trace_reasoning_span_id", "ai_trace_reasoning", ["span_id"])
    op.create_index(
        "ix_ai_trace_reasoning_span_step",
        "ai_trace_reasoning",
        ["span_id", "step_number"],
    )

    # AI Trace Metrics table
    op.create_table(
        "ai_trace_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("bucket_end", sa.DateTime(), nullable=False),
        sa.Column("trace_type", trace_type_enum, nullable=False),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("total_traces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_traces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_traces", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_input_tokens", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("avg_output_tokens", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("avg_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("min_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("max_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("p50_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("p95_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("p99_duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_trace_metrics_bucket_start", "ai_trace_metrics", ["bucket_start"])
    op.create_index(
        "ix_ai_trace_metrics_bucket_type",
        "ai_trace_metrics",
        ["bucket_start", "trace_type"],
    )
    op.create_index(
        "ix_ai_trace_metrics_provider_model",
        "ai_trace_metrics",
        ["provider", "model", "bucket_start"],
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("ai_trace_metrics")
    op.drop_table("ai_trace_reasoning")
    op.drop_table("ai_trace_spans")
    op.drop_table("ai_traces")
    op.drop_table("rankings")
    op.drop_table("ideas")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS span_status")
    op.execute("DROP TYPE IF EXISTS span_type")
    op.execute("DROP TYPE IF EXISTS trace_status")
    op.execute("DROP TYPE IF EXISTS trace_type")
    op.execute("DROP TYPE IF EXISTS idea_status")
    op.execute("DROP TYPE IF EXISTS source_type")
