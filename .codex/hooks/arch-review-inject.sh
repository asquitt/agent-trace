#!/bin/bash

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.user_prompt // .message // empty' 2>/dev/null)

if [ -z "$PROMPT" ]; then
    exit 0
fi

if echo "$PROMPT" | grep -qiE '(implement|add|create|build|fix|refactor|modify|update|review).*(feature|service|agent|task|handler|model|component|module|endpoint|workflow|architecture|codebase)'; then
    cat << 'EOF'
{"additionalContext":"AI TRACE ARCHITECTURE: Read AGENTS.md, CLAUDE.md, and .github/ai-review/senior-review.md. Search routers, models, observability runtime, scheduler, security, tracing providers, migrations, locks, and runbooks. Preserve org_id isolation, advisory-lock leadership, explicit standby and error states, stale-session projection, monotonic activity watermarks, and the development-only dashboard boundary."}
EOF
fi

exit 0
