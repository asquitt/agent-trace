"""Add observability domain enums.

Revision ID: 002
Revises: 001
Create Date: 2026-02-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'deployment_environment') THEN
                CREATE TYPE deployment_environment AS ENUM ('dev', 'staging', 'prod');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'session_status') THEN
                CREATE TYPE session_status AS ENUM ('active', 'idle', 'completed', 'failed', 'terminated');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'action_type') THEN
                CREATE TYPE action_type AS ENUM (
                    'llm_call', 'tool_call', 'delegation', 'memory_read',
                    'memory_write', 'network_call', 'policy_action'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'memory_consistency_state') THEN
                CREATE TYPE memory_consistency_state AS ENUM ('consistent', 'diverged', 'unknown');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'budget_scope_type') THEN
                CREATE TYPE budget_scope_type AS ENUM ('org', 'deployment', 'agent');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'budget_period_type') THEN
                CREATE TYPE budget_period_type AS ENUM ('hour', 'day', 'month');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'policy_action_type') THEN
                CREATE TYPE policy_action_type AS ENUM ('alert', 'throttle', 'require_approval', 'shutdown');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'policy_status') THEN
                CREATE TYPE policy_status AS ENUM ('active', 'paused');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'anomaly_type') THEN
                CREATE TYPE anomaly_type AS ENUM (
                    'api_spike', 'unusual_resource_access', 'memory_divergence',
                    'cost_spike', 'delegation_loop'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'anomaly_severity') THEN
                CREATE TYPE anomaly_severity AS ENUM ('low', 'medium', 'high', 'critical');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'anomaly_status') THEN
                CREATE TYPE anomaly_status AS ENUM ('open', 'acknowledged', 'resolved');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'delegation_status') THEN
                CREATE TYPE delegation_status AS ENUM ('requested', 'accepted', 'rejected', 'completed', 'failed');
            END IF;
        END$$;
    """)


def downgrade() -> None:
    op.execute("DROP TYPE IF EXISTS delegation_status")
    op.execute("DROP TYPE IF EXISTS anomaly_status")
    op.execute("DROP TYPE IF EXISTS anomaly_severity")
    op.execute("DROP TYPE IF EXISTS anomaly_type")
    op.execute("DROP TYPE IF EXISTS policy_status")
    op.execute("DROP TYPE IF EXISTS policy_action_type")
    op.execute("DROP TYPE IF EXISTS budget_period_type")
    op.execute("DROP TYPE IF EXISTS budget_scope_type")
    op.execute("DROP TYPE IF EXISTS memory_consistency_state")
    op.execute("DROP TYPE IF EXISTS action_type")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.execute("DROP TYPE IF EXISTS deployment_environment")
