"""
run.py
──────
CLI entrypoint for the Financial Research Agent.

Usage:
    python run.py "Analyse Apple Inc. investment outlook"
    python run.py --stream "Research Microsoft Azure growth"
    python run.py --cache  "Analyse Apple Inc. investment outlook"
    python run.py --stream --cache "Analyse Tesla investment outlook"

Flags:
    --stream   Stream node-by-node updates as they complete
    --cache    Cache results to .agent_cache/ so repeat queries are instant
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

from dotenv import load_dotenv
load_dotenv()

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.live import Live
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from agent.graph import graph
from agent.nodes.synthesis import synthesis_node
from agent.state import make_initial_state

console = Console() if HAS_RICH else None

CACHE_DIR = pathlib.Path(".agent_cache")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(query: str, max_iterations: int) -> str:
    return hashlib.md5(f"{query}:{max_iterations}".encode()).hexdigest()


def _cache_load(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _cache_save(key: str, result: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    # Only serialise primitive fields — strip non-JSON-safe objects
    safe = {k: v for k, v in result.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    path.write_text(json.dumps(safe, indent=2))


# ── Main entry ────────────────────────────────────────────────────────────────

def run_agent(query: str, stream: bool = False, use_cache: bool = False) -> str:
    """Run the financial research agent. Returns the final report."""
    max_iterations = 8
    cache_key = _cache_key(query, max_iterations)

    if use_cache:
        cached = _cache_load(cache_key)
        if cached:
            if HAS_RICH:
                console.print(Panel(
                    f"[bold cyan]Financial Research Agent[/bold cyan] [dim](cached)[/dim]\n\n"
                    f"[white]Query:[/white] {query}",
                    border_style="cyan",
                ))
                console.print(Markdown(cached.get("final_report", "")))
            else:
                print(cached.get("final_report", ""))
            return cached.get("final_report", "")

    state = make_initial_state(query, max_iterations)

    if HAS_RICH:
        console.print(Panel(
            f"[bold cyan]Financial Research Agent[/bold cyan]\n\n"
            f"[white]Query:[/white] {query}",
            border_style="cyan",
        ))
        console.print()

    if stream:
        report = _run_streaming(state)
    else:
        report = _run_batch(state)

    if use_cache and report:
        _cache_save(cache_key, {"final_report": report, "query": query})

    return report


def _run_batch(state: dict) -> str:
    start = time.time()

    if HAS_RICH:
        with Live(Spinner("dots", text=" Agent thinking..."), refresh_per_second=10):
            result = graph.invoke(state)
            synthesis = synthesis_node(result)
    else:
        print("Running agent...")
        result = graph.invoke(state)
        synthesis = synthesis_node(result)

    elapsed      = time.time() - start
    report       = synthesis.get("final_report", "No report generated.")
    tools_called = result.get("tools_called", [])
    iterations   = result.get("iteration_count", 0)

    if HAS_RICH:
        console.print(Panel(
            f"[green]✓ Complete[/green]  |  "
            f"Iterations: [bold]{iterations}[/bold]  |  "
            f"Tools: [bold]{', '.join(tools_called) or 'none'}[/bold]  |  "
            f"Time: [bold]{elapsed:.1f}s[/bold]",
            border_style="green",
        ))
        console.print()
        console.print(Markdown(report))
    else:
        print(f"\n{'='*60}")
        print(f"Tools called: {tools_called}")
        print(f"Iterations:   {iterations}")
        print(f"Time:         {elapsed:.1f}s")
        print(f"{'='*60}\n")
        print(report)

    return report


def _run_streaming(state: dict) -> str:
    if HAS_RICH:
        console.print("[dim]Streaming mode — updates appear as each node completes[/dim]\n")

    final_state = state

    for event in graph.stream(state, stream_mode="updates"):
        for node_name, node_output in event.items():
            final_state = {**final_state, **node_output}

            if HAS_RICH:
                icon = _node_icon(node_name)
                console.print(f"{icon} [bold]{node_name}[/bold] completed", end="")

                if node_name == "supervisor":
                    tools = node_output.get("tools_remaining", [])
                    console.print(f"  → queuing: [cyan]{tools}[/cyan]")
                    plan = node_output.get("current_plan", "")
                    if plan:
                        console.print(f"   [dim]{plan[:120]}[/dim]")
                elif node_name == "dispatcher":
                    results = node_output.get("tool_results", [])
                    for r in results:
                        status = "[green]✓[/green]" if r["success"] else "[red]✗[/red]"
                        console.print(f"\n  {status} {r['tool_name']}: {r['output'][:80]}…")
                else:
                    console.print()
            else:
                print(f"[{node_name}] completed")

    if HAS_RICH:
        console.print("\n[dim]Synthesising report...[/dim]")

    synthesis = synthesis_node(final_state)
    report = synthesis.get("final_report", "No report generated.")

    if HAS_RICH:
        console.print(Markdown(report))
    else:
        print(report)

    return report


def _node_icon(node_name: str) -> str:
    return {
        "supervisor": "🧠",
        "dispatcher": "⚙️ ",
        "web_search":  "🌐",
        "wikipedia":   "📖",
        "calculator":  "🔢",
        "arxiv":       "📄",
        "sec_edgar":   "🏛️ ",
        "rag_search":  "🔍",
    }.get(node_name, "⚙️ ")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Financial Research Agent powered by Claude + LangGraph"
    )
    parser.add_argument("query", type=str, help='e.g. "Analyse Apple Inc. investment outlook"')
    parser.add_argument("--stream", action="store_true", help="Stream node-by-node updates")
    parser.add_argument("--cache",  action="store_true", help="Cache result to .agent_cache/")
    args = parser.parse_args()

    try:
        run_agent(args.query, stream=args.stream, use_cache=args.cache)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
