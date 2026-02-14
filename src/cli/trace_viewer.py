"""CLI tool for viewing and exploring AI traces.

Usage:
    ai-trace list                    # List recent traces
    ai-trace show <trace_id>         # Show trace details
    ai-trace reasoning <trace_id>    # Show reasoning chain
    ai-trace export <trace_id>       # Export trace to JSON
"""

import asyncio
import json
from typing import Any, Optional
from uuid import UUID

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.tree import Tree

from ..database import async_session_factory
from ..tracing.storage import PostgresStorageBackend

app = typer.Typer(
    name="ai-trace",
    help="AI Trace Viewer - Explore and debug AI decision chains",
    no_args_is_help=True,
)
console = Console()


def get_storage() -> PostgresStorageBackend:
    """Get storage backend."""
    return PostgresStorageBackend(async_session_factory)


def format_duration(ms: Optional[int]) -> str:
    """Format duration in human-readable form."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def format_tokens(input_tokens: int, output_tokens: int) -> str:
    """Format token counts."""
    total = input_tokens + output_tokens
    if total == 0:
        return "-"
    return f"{input_tokens}→{output_tokens}"


def format_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost == 0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def status_color(status: str) -> str:
    """Get color for status."""
    colors = {
        "running": "yellow",
        "completed": "green",
        "failed": "red",
        "timeout": "red",
    }
    return colors.get(status.lower(), "white")


@app.command("list")
def list_traces(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of traces to show"),
    trace_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by trace type"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    idea_id: Optional[int] = typer.Option(None, "--idea", "-i", help="Filter by idea ID"),
) -> None:
    """List recent AI traces."""
    storage = get_storage()

    async def _list() -> None:
        traces = await storage.list_traces(
            trace_type=trace_type,
            status=status,
            idea_id=idea_id,
            limit=limit,
        )

        if not traces:
            console.print("[dim]No traces found[/]")
            return

        table = Table(title="AI Traces", show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim", width=10)
        table.add_column("Type", width=12)
        table.add_column("Status", width=10)
        table.add_column("Idea", width=6)
        table.add_column("Duration", width=10)
        table.add_column("Tokens", width=12)
        table.add_column("Cost", width=8)
        table.add_column("Started", width=18)

        for trace in traces:
            status_str = trace.status.value if hasattr(trace.status, "value") else str(trace.status)
            type_str = trace.trace_type.value if hasattr(trace.trace_type, "value") else str(trace.trace_type)

            table.add_row(
                str(trace.id)[:8] + "...",
                type_str,
                f"[{status_color(status_str)}]{status_str}[/]",
                str(trace.idea_id) if trace.idea_id else "-",
                format_duration(trace.duration_ms),
                format_tokens(trace.total_input_tokens, trace.total_output_tokens),
                format_cost(trace.estimated_cost_usd),
                trace.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            )

        console.print(table)
        console.print(f"\n[dim]Showing {len(traces)} traces[/]")

    asyncio.run(_list())


@app.command("show")
def show_trace(
    trace_id: str = typer.Argument(..., help="Trace ID (UUID)"),
    full: bool = typer.Option(False, "--full", "-f", help="Show full prompts/responses"),
) -> None:
    """Show detailed trace information."""
    storage = get_storage()

    async def _show() -> None:
        try:
            uuid = UUID(trace_id)
        except ValueError:
            console.print(f"[red]Invalid UUID: {trace_id}[/]")
            raise typer.Exit(1) from None

        trace = await storage.get_trace(uuid)
        if not trace:
            console.print(f"[red]Trace not found: {trace_id}[/]")
            raise typer.Exit(1)

        status_str = trace.status.value if hasattr(trace.status, "value") else str(trace.status)
        type_str = trace.trace_type.value if hasattr(trace.trace_type, "value") else str(trace.trace_type)

        # Build trace tree
        tree = Tree(
            f"[bold cyan]Trace[/] {str(trace.id)[:8]}... "
            f"[{status_color(status_str)}]({status_str})[/]"
        )

        # Trace info branch
        info = tree.add("[bold]Info[/]")
        info.add(f"Type: {type_str}")
        info.add(f"Duration: {format_duration(trace.duration_ms)}")
        info.add(f"Tokens: {format_tokens(trace.total_input_tokens, trace.total_output_tokens)}")
        info.add(f"Cost: {format_cost(trace.estimated_cost_usd)}")
        if trace.idea_id:
            info.add(f"Idea ID: {trace.idea_id}")
        if trace.tags:
            info.add(f"Tags: {', '.join(trace.tags)}")

        # Spans branch
        spans_branch = tree.add(f"[bold]Spans[/] ({len(trace.spans or [])})")

        for span in trace.spans or []:
            span_status = span.status.value if hasattr(span.status, "value") else str(span.status)
            span_type = span.span_type.value if hasattr(span.span_type, "value") else str(span.span_type)

            span_label = (
                f"[cyan]{span.name}[/] "
                f"[dim]({span_type})[/] "
                f"[{status_color(span_status)}]{span_status}[/] "
                f"[dim]{format_duration(span.duration_ms)}[/]"
            )
            span_node = spans_branch.add(span_label)

            if span.provider:
                span_node.add(f"Provider: {span.provider} / {span.model or 'unknown'}")
            if span.input_tokens or span.output_tokens:
                span_node.add(f"Tokens: {format_tokens(span.input_tokens, span.output_tokens)}")

            # Reasoning steps
            if span.reasoning_steps:
                reasoning_node = span_node.add(f"[bold]Reasoning[/] ({len(span.reasoning_steps)} steps)")
                for r in span.reasoning_steps:
                    step_label = f"[dim]{r.step_number}.[/] {r.step_type}: {r.description[:60]}..."
                    step_node = reasoning_node.add(step_label)
                    if r.dimension:
                        step_node.add(
                            f"Score: {r.raw_score:.0f}/100 × {r.weight_applied:.2f} = {r.weighted_score:.1f}"
                        )
                    if r.explanation and full:
                        step_node.add(f"[italic]{r.explanation}[/]")

            # Full content
            if full and span.system_prompt:
                span_node.add(
                    Panel(
                        Syntax(span.system_prompt[:500] + "..." if len(span.system_prompt) > 500 else span.system_prompt, "text"),
                        title="System Prompt",
                        border_style="dim",
                    )
                )
            if full and span.assistant_response:
                span_node.add(
                    Panel(
                        span.assistant_response[:500] + "..." if len(span.assistant_response) > 500 else span.assistant_response,
                        title="Response",
                        border_style="dim",
                    )
                )

        # Error info
        if trace.error_message:
            error_branch = tree.add("[bold red]Error[/]")
            error_branch.add(f"Type: {trace.error_type}")
            error_branch.add(f"Message: {trace.error_message}")

        console.print(tree)

    asyncio.run(_show())


@app.command("reasoning")
def show_reasoning(
    trace_id: str = typer.Argument(..., help="Trace ID (UUID)"),
) -> None:
    """Show reasoning chain for a trace."""
    storage = get_storage()

    async def _reasoning() -> None:
        try:
            uuid = UUID(trace_id)
        except ValueError:
            console.print(f"[red]Invalid UUID: {trace_id}[/]")
            raise typer.Exit(1) from None

        trace = await storage.get_trace(uuid)
        if not trace:
            console.print(f"[red]Trace not found: {trace_id}[/]")
            raise typer.Exit(1)

        console.print(f"[bold cyan]Reasoning Chain[/] for trace {str(trace.id)[:8]}...")
        console.print()

        step_num = 0
        for span in trace.spans or []:
            for r in span.reasoning_steps or []:
                step_num += 1

                # Step header
                console.print(f"[bold]{step_num}. {r.step_type}[/] [dim]({span.name})[/]")
                console.print(f"   {r.description}")

                if r.dimension:
                    console.print(f"   [cyan]Dimension:[/] {r.dimension}")
                    console.print(
                        f"   [cyan]Score:[/] {r.raw_score:.0f}/100 × {r.weight_applied:.2f} = [bold]{r.weighted_score:.1f}[/]"
                    )

                if r.explanation:
                    console.print(f"   [italic dim]{r.explanation}[/]")

                console.print()

        if step_num == 0:
            console.print("[dim]No reasoning steps found[/]")

    asyncio.run(_reasoning())


@app.command("export")
def export_trace(
    trace_id: str = typer.Argument(..., help="Trace ID (UUID)"),
    output: str = typer.Option("trace.json", "--output", "-o", help="Output file path"),
    include_prompts: bool = typer.Option(True, "--prompts/--no-prompts", help="Include prompts/responses"),
) -> None:
    """Export trace to JSON file."""
    storage = get_storage()

    async def _export() -> None:
        try:
            uuid = UUID(trace_id)
        except ValueError:
            console.print(f"[red]Invalid UUID: {trace_id}[/]")
            raise typer.Exit(1) from None

        trace = await storage.get_trace(uuid)
        if not trace:
            console.print(f"[red]Trace not found: {trace_id}[/]")
            raise typer.Exit(1)

        # Build export data
        export_data: dict[str, Any] = {
            "trace": {
                "id": str(trace.id),
                "correlation_id": str(trace.correlation_id),
                "trace_type": trace.trace_type.value if hasattr(trace.trace_type, "value") else str(trace.trace_type),
                "status": trace.status.value if hasattr(trace.status, "value") else str(trace.status),
                "idea_id": trace.idea_id,
                "started_at": trace.started_at.isoformat(),
                "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
                "duration_ms": trace.duration_ms,
                "total_input_tokens": trace.total_input_tokens,
                "total_output_tokens": trace.total_output_tokens,
                "estimated_cost_usd": trace.estimated_cost_usd,
                "tags": trace.tags or [],
            },
            "spans": [],
        }

        for span in trace.spans or []:
            span_data: dict[str, Any] = {
                "id": str(span.id),
                "name": span.name,
                "span_type": span.span_type.value if hasattr(span.span_type, "value") else str(span.span_type),
                "provider": span.provider,
                "model": span.model,
                "started_at": span.started_at.isoformat(),
                "completed_at": span.completed_at.isoformat() if span.completed_at else None,
                "duration_ms": span.duration_ms,
                "input_tokens": span.input_tokens,
                "output_tokens": span.output_tokens,
                "status": span.status.value if hasattr(span.status, "value") else str(span.status),
                "reasoning_steps": [
                    {
                        "step_number": r.step_number,
                        "step_type": r.step_type,
                        "description": r.description,
                        "dimension": r.dimension,
                        "raw_score": r.raw_score,
                        "weighted_score": r.weighted_score,
                        "explanation": r.explanation,
                    }
                    for r in (span.reasoning_steps or [])
                ],
            }

            if include_prompts:
                span_data["system_prompt"] = span.system_prompt
                span_data["user_prompt"] = span.user_prompt
                span_data["assistant_response"] = span.assistant_response

            export_data["spans"].append(span_data)

        with open(output, "w") as f:
            json.dump(export_data, f, indent=2)

        console.print(f"[green]Exported trace to {output}[/]")

    asyncio.run(_export())


@app.command("stats")
def show_stats() -> None:
    """Show trace statistics summary."""
    storage = get_storage()

    async def _stats() -> None:
        total = await storage.get_trace_count()
        completed = await storage.get_trace_count(status="completed")
        failed = await storage.get_trace_count(status="failed")
        running = await storage.get_trace_count(status="running")

        table = Table(title="Trace Statistics", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Total Traces", str(total))
        table.add_row("Completed", f"[green]{completed}[/]")
        table.add_row("Failed", f"[red]{failed}[/]")
        table.add_row("Running", f"[yellow]{running}[/]")

        if total > 0:
            success_rate = (completed / total) * 100
            table.add_row("Success Rate", f"{success_rate:.1f}%")

        console.print(table)

    asyncio.run(_stats())


if __name__ == "__main__":
    app()
