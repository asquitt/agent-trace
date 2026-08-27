ARG PYTHON_IMAGE=python:3.11.15-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md ./
COPY requirements/build.lock ./requirements/build.lock
COPY src/ ./src/

RUN pip install --no-deps --require-hashes --requirement requirements/build.lock \
    && pip wheel --no-deps --no-build-isolation --wheel-dir /wheelhouse .

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements/production.lock ./requirements/production.lock
RUN pip install --no-deps --require-hashes --requirement requirements/production.lock

COPY --from=builder /wheelhouse/ /wheelhouse/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && pip install --no-deps /wheelhouse/*.whl \
    && pip check \
    && rm -rf /wheelhouse \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=5).close()"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
