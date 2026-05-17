"""
run.py
──────
CLI entrypoint for the Financial Research Agent.

Usage:
    python run.py "Analyse Apple Inc. investment outlook"
    python run.py "What are the risks of investing in JPMorgan Chase?"
    python run.py --stream "Research Microsoft Azure growth"
"""

from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv
load_dotenv()

# Rich for pretty terminal output
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
from agent.state import make_initial_state

console = Console() if HAS_RICH else None


def run_agent(query: str, stream: bool = False) -> str:
    """
    Run the financial research agent on a query.
    Returns the final report as a string.
    """
    state = make_initial_state(query)

    if HAS_RICH:
        console.print(Panel(
            f"[bold cyan]Financial Research Agent[/bold cyan]\n\n"
            f"[white]Query:[/white] {query}",
            border_style="cyan",
        ))
        console.print()

    if stream:
        return _run_streaming(state)
    else:
        return _run_batch(state)


def _run_batch(state: dict) -> str:
    """Run the graph and display results after completion."""
    start = time.time()

    if HAS_RICH:
        with Live(Spinner("dots", text=" Agent thinking..."), refresh_per_second=10):
            result = graph.invoke(state)
    else:
        print("Running agent...")
        result = graph.invoke(state)

    elapsed = time.time() - start

    report = result.get("final_report", "No report generated.")
    tools_called = result.get("tools_called", [])
    iterations = result.get("iteration_count", 0)

    if HAS_RICH:
        # Stats panel
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
    """Stream node-by-node updates as the graph executes."""
    if HAS_RICH:
        console.print("[dim]Streaming mode — updates appear as each node completes[/dim]\n")

    final_report = ""

    for event in graph.stream(state, stream_mode="updates"):
        for node_name, node_output in event.items():

            if HAS_RICH:
                # Show which node just ran
                icon = _node_icon(node_name)
                console.print(f"{icon} [bold]{node_name}[/bold] completed", end="")

                # Show what the node decided / found
                if node_name == "supervisor":
                    plan = node_output.get("current_plan", "")
                    tools = node_output.get("tools_remaining", [])
                    console.print(f"  → queuing: [cyan]{tools}[/cyan]")
                    if plan:
                        console.print(f"   [dim]{plan[:120]}[/dim]")

                elif node_name in ("web_search", "wikipedia", "calculator", "arxiv"):
                    results = node_output.get("tool_results", [])
                    if results:
                        last = results[-1]
                        status = "[green]✓[/green]" if last["success"] else "[red]✗[/red]"
                        console.print(f"  {status} {last['output'][:100]}…")
                    else:
                        console.print()

                elif node_name == "synthesis":
                    console.print()
                    final_report = node_output.get("final_report", "")
            else:
                print(f"[{node_name}] completed")

    if HAS_RICH and final_report:
        console.print()
        console.print(Markdown(final_report))

    return final_report


def _node_icon(node_name: str) -> str:
    icons = {
        "supervisor": "🧠",
        "web_search": "🌐",
        "wikipedia": "📖",
        "calculator": "🔢",
        "arxiv": "📄",
        "synthesis": "✍️ ",
    }
    return icons.get(node_name, "⚙️ ")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Financial Research Agent powered by Claude + LangGraph"
    )
    parser.add_argument(
        "query",
        type=str,
        help='Research query, e.g. "Analyse Apple Inc. investment outlook"',
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream node-by-node updates instead of waiting for the full result",
    )
    args = parser.parse_args()

    try:
        run_agent(args.query, stream=args.stream)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)