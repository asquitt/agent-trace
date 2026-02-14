#!/usr/bin/env python3
"""Synthetic performance gate for core observability APIs."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx


@dataclass
class Sample:
    endpoint: str
    status_code: int
    duration_ms: float
    ok: bool
    error: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = math.ceil((percentile / 100.0) * len(sorted_values)) - 1
    index = min(max(rank, 0), len(sorted_values) - 1)
    return sorted_values[index]


async def _timed_request(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> Sample:
    start = perf_counter()
    try:
        response = await client.request(method, path, **kwargs)
        elapsed_ms = (perf_counter() - start) * 1000.0
        ok = 200 <= response.status_code < 300
        error = None if ok else response.text[:300]
        return Sample(
            endpoint=endpoint,
            status_code=response.status_code,
            duration_ms=elapsed_ms,
            ok=ok,
            error=error,
        )
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        elapsed_ms = (perf_counter() - start) * 1000.0
        return Sample(
            endpoint=endpoint,
            status_code=0,
            duration_ms=elapsed_ms,
            ok=False,
            error=str(exc),
        )


async def _bounded_gather(
    coros: list[asyncio.Future[Sample] | asyncio.Task[Sample] | Any],
    *,
    concurrency: int,
) -> list[Sample]:
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def _run(coro: Any) -> Sample:
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_run(coro) for coro in coros])


async def run_perf_gate(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    base_url = args.base_url.rstrip("/")
    org_id = args.org_id or f"perf-{uuid4().hex[:8]}"
    deployment_key = f"perf-deploy-{uuid4().hex[:8]}"
    started_at = datetime.now(UTC)

    samples: list[Sample] = []
    endpoint_latencies: dict[str, list[float]] = defaultdict(list)
    endpoint_failures: dict[str, int] = defaultdict(int)
    endpoint_calls: dict[str, int] = defaultdict(int)

    timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:
        ready = await _timed_request(client, endpoint="health_ready", method="GET", path="/health/ready")
        samples.append(ready)
        if not ready.ok:
            report = {
                "started_at": started_at.isoformat(),
                "base_url": base_url,
                "org_id": org_id,
                "status": "failed",
                "reason": "health_ready_failed",
                "health_ready": {
                    "status_code": ready.status_code,
                    "error": ready.error,
                    "duration_ms": ready.duration_ms,
                },
            }
            return report, False

        deployment_payload = {
            "org_id": org_id,
            "deployment_key": deployment_key,
            "name": "Perf Deployment",
            "environment": "prod",
            "runtime": "perf-harness",
            "runtime_version": "1.0.0",
            "metadata": {"suite": "perf_gate"},
        }
        deployment_start = perf_counter()
        deployment_response = await client.post(
            "/api/v1/observability/deployments",
            json=deployment_payload,
        )
        deployment_elapsed_ms = (perf_counter() - deployment_start) * 1000.0
        deployment_sample = Sample(
            endpoint="create_deployment",
            status_code=deployment_response.status_code,
            duration_ms=deployment_elapsed_ms,
            ok=200 <= deployment_response.status_code < 300,
            error=None if 200 <= deployment_response.status_code < 300 else deployment_response.text[:300],
        )
        samples.append(deployment_sample)
        if not deployment_sample.ok:
            report = {
                "started_at": started_at.isoformat(),
                "base_url": base_url,
                "org_id": org_id,
                "status": "failed",
                "reason": "deployment_create_failed",
                "deployment_response": {
                    "status_code": deployment_sample.status_code,
                    "error": deployment_sample.error,
                },
            }
            return report, False

        deployment_id = deployment_response.json()["id"]

        session_ids: list[str] = []
        for idx in range(args.session_count):
            session_payload = {
                "deployment_id": deployment_id,
                "agent_id": f"perf-agent-{idx}",
                "started_at": _utc_now_iso(),
                "tags": ["perf"],
                "metadata": {"suite": "perf_gate"},
            }
            session_start = perf_counter()
            session_response = await client.post(
                "/api/v1/observability/sessions",
                json=session_payload,
            )
            session_elapsed_ms = (perf_counter() - session_start) * 1000.0
            session_sample = Sample(
                endpoint="create_session",
                status_code=session_response.status_code,
                duration_ms=session_elapsed_ms,
                ok=200 <= session_response.status_code < 300,
                error=None if 200 <= session_response.status_code < 300 else session_response.text[:300],
            )
            samples.append(session_sample)
            if not session_sample.ok:
                report = {
                    "started_at": started_at.isoformat(),
                    "base_url": base_url,
                    "org_id": org_id,
                    "status": "failed",
                    "reason": "session_create_failed",
                    "session_response": {
                        "status_code": session_sample.status_code,
                        "error": session_sample.error,
                    },
                }
                return report, False
            session_ids.append(session_response.json()["id"])

        from_ts = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        to_ts = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()

        def _action_payload(index: int) -> dict[str, Any]:
            events = []
            now_iso = _utc_now_iso()
            for event_idx in range(args.events_per_batch):
                events.append(
                    {
                        "client_event_id": str(uuid4()),
                        "action_type": "tool_call",
                        "action_name": f"perf-action-{index}-{event_idx}",
                        "resource": "https://api.example.com/perf",
                        "occurred_at": now_iso,
                        "input_tokens": 24,
                        "output_tokens": 12,
                        "estimated_cost_usd": 0.00012,
                        "latency_ms": 45,
                        "success": True,
                        "metadata": {"perf": True},
                    }
                )
            return {"session_id": session_ids[index % len(session_ids)], "events": events}

        write_requests = [
            _timed_request(
                client,
                endpoint="actions_batch",
                method="POST",
                path="/api/v1/observability/actions/batch",
                params={"evaluate_policies": "false"},
                json=_action_payload(idx),
            )
            for idx in range(args.batch_requests)
        ]
        write_samples = await _bounded_gather(write_requests, concurrency=args.concurrency)
        samples.extend(write_samples)

        read_requests = []
        for _ in range(args.read_rounds):
            read_requests.extend(
                [
                    _timed_request(
                        client,
                        endpoint="dashboard_fleet",
                        method="GET",
                        path="/api/v1/observability/dashboard/fleet",
                        params={
                            "org_id": org_id,
                            "from": from_ts,
                            "to": to_ts,
                            "granularity": "5m",
                        },
                    ),
                    _timed_request(
                        client,
                        endpoint="anomaly_groups",
                        method="GET",
                        path="/api/v1/observability/anomalies/groups",
                        params={"org_id": org_id, "from": from_ts, "to": to_ts},
                    ),
                    _timed_request(
                        client,
                        endpoint="cost_summary",
                        method="GET",
                        path="/api/v1/observability/costs/summary",
                        params={"org_id": org_id, "from": from_ts, "to": to_ts},
                    ),
                    _timed_request(
                        client,
                        endpoint="active_sessions",
                        method="GET",
                        path="/api/v1/observability/sessions/active",
                        params={"org_id": org_id, "page": 1, "page_size": 25},
                    ),
                    _timed_request(
                        client,
                        endpoint="risk_insights",
                        method="GET",
                        path="/api/v1/observability/insights/risk",
                        params={"org_id": org_id, "from": from_ts, "to": to_ts},
                    ),
                ]
            )
        read_samples = await _bounded_gather(read_requests, concurrency=args.concurrency)
        samples.extend(read_samples)

    for sample in samples:
        endpoint_calls[sample.endpoint] += 1
        endpoint_latencies[sample.endpoint].append(sample.duration_ms)
        if not sample.ok:
            endpoint_failures[sample.endpoint] += 1

    endpoint_summary: dict[str, dict[str, Any]] = {}
    for endpoint, latencies in endpoint_latencies.items():
        calls = endpoint_calls[endpoint]
        failures = endpoint_failures.get(endpoint, 0)
        endpoint_summary[endpoint] = {
            "calls": calls,
            "failures": failures,
            "error_rate": (failures / calls) if calls else 0.0,
            "p50_ms": round(_percentile(latencies, 50), 2),
            "p95_ms": round(_percentile(latencies, 95), 2),
            "p99_ms": round(_percentile(latencies, 99), 2),
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            "min_ms": round(min(latencies), 2) if latencies else 0.0,
        }

    gate_failures: list[str] = []
    write_p95 = endpoint_summary.get("actions_batch", {}).get("p95_ms", 0.0)
    if write_p95 > args.write_p95_threshold_ms:
        gate_failures.append(
            f"actions_batch p95 {write_p95:.2f}ms exceeded threshold {args.write_p95_threshold_ms:.2f}ms"
        )

    for endpoint in ("dashboard_fleet", "anomaly_groups", "cost_summary", "active_sessions", "risk_insights"):
        endpoint_p95 = endpoint_summary.get(endpoint, {}).get("p95_ms", 0.0)
        if endpoint_p95 > args.read_p95_threshold_ms:
            gate_failures.append(
                f"{endpoint} p95 {endpoint_p95:.2f}ms exceeded threshold {args.read_p95_threshold_ms:.2f}ms"
            )

    total_calls = len(samples)
    total_failures = sum(1 for sample in samples if not sample.ok)
    total_error_rate = (total_failures / total_calls) if total_calls else 0.0
    if total_error_rate > args.max_error_rate:
        gate_failures.append(
            f"total error rate {total_error_rate:.4f} exceeded threshold {args.max_error_rate:.4f}"
        )

    completed_at = datetime.now(UTC)
    duration_seconds = (completed_at - started_at).total_seconds()
    report = {
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(duration_seconds, 2),
        "base_url": base_url,
        "org_id": org_id,
        "config": {
            "session_count": args.session_count,
            "batch_requests": args.batch_requests,
            "events_per_batch": args.events_per_batch,
            "read_rounds": args.read_rounds,
            "concurrency": args.concurrency,
            "write_p95_threshold_ms": args.write_p95_threshold_ms,
            "read_p95_threshold_ms": args.read_p95_threshold_ms,
            "max_error_rate": args.max_error_rate,
        },
        "totals": {
            "calls": total_calls,
            "failures": total_failures,
            "error_rate": round(total_error_rate, 4),
        },
        "endpoints": endpoint_summary,
        "status": "passed" if not gate_failures else "failed",
        "gate_failures": gate_failures,
    }
    return report, not gate_failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthetic load/performance gate for AI Trace APIs")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--org-id", default="")
    parser.add_argument("--session-count", type=int, default=12)
    parser.add_argument("--batch-requests", type=int, default=120)
    parser.add_argument("--events-per-batch", type=int, default=8)
    parser.add_argument("--read-rounds", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--write-p95-threshold-ms", type=float, default=900.0)
    parser.add_argument("--read-p95-threshold-ms", type=float, default=750.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument(
        "--report-dir",
        default="docs/reports/perf",
        help="Directory to write JSON report artifacts",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, passed = asyncio.run(run_perf_gate(args))

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"perf-gate-{timestamp}.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    print(f"\nReport saved: {report_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
