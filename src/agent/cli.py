"""
Layer 3: CLI / REPL.

Two modes:

  * Single-shot:  uv run agent "your question here"
                  Answers once and exits. Useful for scripting and CI.

  * REPL:         uv run agent
                  Drops into an interactive prompt. Conversation history
                  persists across questions, so follow-ups work naturally
                  ('what about issues?' refers back to the prior project).

Special commands in REPL mode:
    /reset, /clear   — clear conversation history (start fresh)
    /help            — show this help
    exit, quit       — exit the REPL (Ctrl+C also works)

Output uses ASCII status markers (no ANSI colors) for cross-terminal
portability. If we need colors for the demo, that's a Day-3 polish.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from agent.loop import (
    AgentEvent,
    AgentLoop,
    ErrorEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.mcp_client import MCPClientAdapter
from gitlab_mcp.config import get_settings

# ----- Output helpers --------------------------------------------------------

def _format_args(args: dict) -> str:
    """Render tool arguments compactly for the terminal."""
    if not args:
        return ""
    parts = [f"{k}={v!r}" for k, v in args.items()]
    return ", ".join(parts)


def _print_event(event: AgentEvent) -> None:
    """Render one event to the terminal."""
    if isinstance(event, ToolCallEvent):
        args = _format_args(event.arguments)
        print(f"  🔧 {event.name}({args})", flush=True)
    elif isinstance(event, ToolResultEvent):
        marker = "✗" if event.is_error else "✓"
        # One-line preview of the result. Real result already went to Claude.
        preview = event.result_preview.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:80] + "..."
        print(f"     {marker} {preview}", flush=True)
    elif isinstance(event, TextEvent):
        # Blank line separates Claude's prose from the tool-call trace
        # that preceded it.
        print()
        print(event.text, flush=True)
    elif isinstance(event, ErrorEvent):
        print(f"\n[error] {event.message}", flush=True)


async def _run_one_question(loop: AgentLoop, question: str) -> None:
    """Drive a single question through the loop, printing events as they arrive."""
    print("🤔 thinking...", flush=True)
    async for event in loop.ask(question):
        _print_event(event)


# ----- REPL ------------------------------------------------------------------

REPL_HELP = """\
Type a question about your GitLab workspace. The agent has tools for
projects, merge requests, issues, pipelines, and user activity.

Special commands:
  /reset, /clear   — clear conversation history
  /help            — show this help
  exit, quit       — exit the REPL (Ctrl+C also works)
"""


async def _repl(loop: AgentLoop) -> int:
    """Interactive prompt loop. Returns process exit code."""
    print("gitlab-mcp agent — type a question or /help")

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            # Ctrl+D / Ctrl+Z — clean exit.
            print()
            return 0
        except KeyboardInterrupt:
            # Ctrl+C at the prompt — clean exit, not a crash.
            print()
            return 0

        if not line:
            continue

        if line.lower() in {"exit", "quit"}:
            return 0
        if line in {"/help", "/?"}:
            print(REPL_HELP)
            continue
        if line in {"/reset", "/clear"}:
            loop.reset()
            print("✓ conversation cleared")
            continue

        try:
            await _run_one_question(loop, line)
        except KeyboardInterrupt:
            # Ctrl+C mid-question: don't kill the REPL, just abandon the question.
            # Anthropic SDK exceptions raise CancelledError under the hood;
            # we catch the surface KeyboardInterrupt here.
            print("\n[interrupted]")
            continue


# ----- Entry point -----------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Ask questions about your GitLab workspace via Claude + MCP.",
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="Optional one-off question. If omitted, drops into a REPL.",
    )
    parser.add_argument(
        "--server-command",
        default="uv run gitlab-mcp",
        help="Command used to spawn the MCP server (default: 'uv run gitlab-mcp').",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show INFO-level logs (every API call, every tool invocation).",
    )
    return parser.parse_args(argv)


async def _async_main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s %(message)s",
    )

    settings = get_settings()
    server_cmd = args.server_command.split()

    async with MCPClientAdapter(server_cmd) as mcp:
        loop = AgentLoop(settings=settings, mcp=mcp)

        if args.question:
            # Single-shot mode.
            question = " ".join(args.question)
            await _run_one_question(loop, question)
            return 0

        # REPL mode.
        return await _repl(loop)


def main() -> None:
    """Console-script entry point."""
    try:
        exit_code = asyncio.run(_async_main())
    except KeyboardInterrupt:
        # Ctrl+C during shutdown — not an error.
        exit_code = 0
    except Exception as exc:  # pragma: no cover  (top-level safety net)
        # Surface unexpected errors plainly without a Python traceback the
        # user can't act on. INFO logs already capture the detail.
        print(f"\nerror: {exc}", file=sys.stderr)
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()