"""Outbound application ports."""

from __future__ import annotations

from typing import Protocol

from smoke_runner.domain.report_models import ReportCommentaryInput


class ReportCommentator(Protocol):
    """Optional provider of a short comment after deterministic report generation."""

    async def comment(self, summary: ReportCommentaryInput) -> str | None:
        """Return an optional comment without modifying numeric report facts."""
        ...
