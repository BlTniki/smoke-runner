"""MVP adapter that keeps external AI commentary disabled."""

from __future__ import annotations

from smoke_runner.domain.report_models import ReportCommentaryInput


class DisabledReportCommentator:
    """No-op implementation used until explicit user opt-in exists."""

    async def comment(self, summary: ReportCommentaryInput) -> None:
        del summary
        return None
