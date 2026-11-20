"""Terminal chat with an agent whose tools come from MCP servers.

Usage:
    python main.py                         # interactive chat
    python main.py "List my notes"         # one-shot question
    python main.py --list-tools            # show discovered tools and exit
    python main.py --config other.json     # use a different server list
    python main.py --yes                   # auto-approve risky tools (careful!)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import anyio
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from mcp_agent import Agent, MCPHub, ToolEvent, load_config
from mcp_agent.config import ConfigError
from mcp_agent.demo import SUGGESTIONS
from mcp_agent.providers import make_client

console = Console(highlight=False)


def show_tool_call(event: ToolEvent) -> None:
    server, _, tool = event.name.partition("__")
    args = json.dumps(event.arguments, ensure_ascii=False)
    if not event.approved:
        console.print(f"  [red]✋ {server} › {tool}[/red] [dim]{args}  declined[/dim]")
        return
    colour = "red" if event.result.startswith("ERROR") else "green"
    width = max(40, console.width - 10)
    preview = event.result if len(event.result) <= width else event.result[: width - 3] + "..."
    console.print(f"  [{colour}]🔌 {server} › {tool}[/{colour}] [dim]{args}  {event.seconds * 1000:.0f} ms[/dim]")
    console.print(f"     [dim]↳ {preview}[/dim]", soft_wrap=False)


def ask_approval(name: str, arguments: dict[str, Any]) -> bool:
    server, _, tool = name.partition("__")
    console.print(
        Panel(
            f"[bold]{server} › {tool}[/bold] {json.dumps(arguments)}\n"
            "[dim]This MCP tool is marked as destructive.[/dim]",
            title="⚠ Approval needed",
            border_style="yellow",
            expand=False,
        )
    )
    return Confirm.ask("  Allow it?", default=False, console=console)


def servers_table(hub: MCPHub) -> Table:
    table = Table(title="MCP servers", title_justify="left", show_edge=False, header_style="bold")
    table.add_column("server")
    table.add_column("status")
    table.add_column("tools")
    for s in hub.status:
        if s.ok:
            names = ", ".join(t.tool.name for t in hub.tools.values() if t.server == s.name)
            table.add_row(f"[cyan]{s.name}[/cyan]", "[green]connected[/green]", names)
        else:
            table.add_row(f"[cyan]{s.name}[/cyan]", "[red]failed[/red]", f"[red]{s.error}[/red]")
    return table


def tools_table(hub: MCPHub) -> Table:
    table = Table(title="Discovered tools", title_justify="left", show_edge=False, header_style="bold")
    table.add_column("name the model sees")
    table.add_column("approval")
    table.add_column("description")
    for name, t in hub.tools.items():
        approval = "[yellow]asks first[/yellow]" if t.needs_approval else "[dim]auto[/dim]"
        table.add_row(f"[cyan]{name}[/cyan]", approval, t.tool.description or "")
    return table


async def run(args: argparse.Namespace) -> None:
    setup = make_client()
    try:
        servers = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}") from exc

    with open("mcp-servers.log", "a", encoding="utf-8") as errlog:
        async with MCPHub(servers, errlog=errlog) as hub:
            if args.list_tools:
                console.print(tools_table(hub))
                return

            if not args.question:
                console.print(f"[bold green]MCP client agent[/bold green] using [cyan]{setup.model}[/cyan]\n")
            if setup.demo:
                console.print(
                    Panel(
                        f"[bold]Demo mode: scripted AI replies[/bold] ({setup.notice}). "
                        "MCP servers and tool calls are real; only the model is scripted.",
                        border_style="yellow",
                        expand=False,
                    )
                )
            if not args.question:
                console.print(servers_table(hub))
                console.print()

            if not hub.tools:
                console.print("[red]No tools available: every MCP server failed to start. See mcp-servers.log.[/red]")

            agent = Agent(
                client=setup.client,
                model=setup.model,
                host=hub,
                approve=(lambda _n, _a: True) if args.yes else ask_approval,
                on_tool_call=show_tool_call,
            )

            if args.question:
                console.print(f"[bold]you ›[/bold] {args.question}")
                console.print(f"[bold blue]agent ›[/bold blue] {await agent.ask(args.question)}")
                return

            if setup.demo:
                console.print("[dim]Try: " + " · ".join(SUGGESTIONS[1:4]) + "[/dim]")
            console.print("[dim]Commands: /tools  /servers  exit[/dim]\n")
            while True:
                try:
                    user_input = (await anyio.to_thread.run_sync(lambda: console.input("[bold]you › [/bold]"))).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if user_input.lower() in {"exit", "quit"}:
                    break
                if user_input == "/tools":
                    console.print(tools_table(hub))
                elif user_input == "/servers":
                    console.print(servers_table(hub))
                elif user_input:
                    answer = await agent.ask(user_input)
                    console.print(f"[bold blue]agent ›[/bold blue] {answer}\n")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Chat with an agent that uses tools from any MCP server.")
    parser.add_argument("question", nargs="*", help="ask one question and exit")
    parser.add_argument("--config", default=os.getenv("MCP_CONFIG", "servers.json"), help="MCP server list (JSON)")
    parser.add_argument("--list-tools", action="store_true", help="print the discovered tools and exit")
    parser.add_argument("--yes", action="store_true", help="auto-approve tools marked destructive")
    args = parser.parse_args()
    args.question = " ".join(args.question)
    args.config = str(Path(args.config))
    anyio.run(run, args)


if __name__ == "__main__":
    main()
