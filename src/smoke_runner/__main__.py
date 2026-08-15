"""Command-line entry point for Smoke Runner."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smoke_runner import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without reading application settings."""
    parser = argparse.ArgumentParser(
        prog="smoke-runner",
        description="Telegram bot for tracking progress while quitting vaping.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, then load settings and start polling."""
    parser = build_parser()
    parser.parse_args(argv)
    from smoke_runner.bootstrap import run
    from smoke_runner.config import Settings

    run(Settings())  # type: ignore[call-arg]  # Values come from the settings environment.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
