"""Logging setup for TheNightOps with rich console output."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Custom theme for TheNightOps
NIGHTOPS_THEME = Theme(
    {
        "nightops.title": "bold magenta",
        "nightops.info": "cyan",
        "nightops.success": "bold green",
        "nightops.warning": "bold yellow",
        "nightops.error": "bold red",
        "nightops.agent": "bold blue",
        "nightops.mcp": "bold cyan",
        "nightops.finding": "yellow",
        "nightops.timestamp": "dim white",
    }
)

console = Console(theme=NIGHTOPS_THEME)


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging with rich output."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_time=True,
                show_path=verbose,
            )
        ],
    )

    logger = logging.getLogger("nightops")
    logger.setLevel(level)
    return logger


def print_banner() -> None:
    """Print the TheNightOps startup banner."""
    console.print(
        """
[nightops.title]
  ████████╗██╗  ██╗███████╗
  ╚══██╔══╝██║  ██║██╔════╝
     ██║   ███████║█████╗
     ██║   ██╔══██║██╔══╝
     ██║   ██║  ██║███████╗
     ╚═╝   ╚═╝  ╚═╝╚══════╝
  ███╗   ██╗██╗ ██████╗ ██╗  ██╗████████╗ ██████╗ ██████╗ ███████╗
  ████╗  ██║██║██╔════╝ ██║  ██║╚══██╔══╝██╔═══██╗██╔══██╗██╔════╝
  ██╔██╗ ██║██║██║  ███╗███████║   ██║   ██║   ██║██████╔╝███████╗
  ██║╚██╗██║██║██║   ██║██╔══██║   ██║   ██║   ██║██╔═══╝ ╚════██║
  ██║ ╚████║██║╚██████╔╝██║  ██║   ██║   ╚██████╔╝██║     ███████║
  ╚═╝  ╚═══╝╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝     ╚══════╝
[/nightops.title]
[nightops.info]  Your Autonomous SRE Agent — The Incidents Don't Wait Until Morning[/nightops.info]
""",
        highlight=False,
    )
