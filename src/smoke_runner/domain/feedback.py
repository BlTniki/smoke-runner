"""Supportive feedback facts and non-repeating template selection."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.interval_policy import ReactionClass
from smoke_runner.domain.timeline import SessionAssessment


@dataclass(frozen=True, slots=True)
class FeedbackFacts:
    """Precalculated facts available to a feedback renderer."""

    reaction: ReactionClass
    target_at: UtcInstant | None
    next_target_at: UtcInstant
    earliness: timedelta
    lateness: timedelta

    @classmethod
    def from_assessment(cls, assessment: SessionAssessment) -> FeedbackFacts:
        return cls(
            reaction=assessment.reaction,
            target_at=assessment.target_at,
            next_target_at=assessment.next_target_at,
            earliness=assessment.earliness,
            lateness=assessment.lateness,
        )


@dataclass(frozen=True, slots=True)
class FeedbackTemplate:
    """A stable template key and its fact placeholders."""

    key: str
    reaction: ReactionClass
    text: str


class TemplateIndexChooser(Protocol):
    """Injectable source of variation for template selection."""

    def choose(self, candidate_count: int) -> int:
        """Return an index in ``range(candidate_count)``."""
        ...


class RandomTemplateIndexChooser:
    """Production chooser backed by the standard-library CSPRNG."""

    def choose(self, candidate_count: int) -> int:
        return secrets.randbelow(candidate_count)


FEEDBACK_TEMPLATES: dict[ReactionClass, tuple[FeedbackTemplate, ...]] = {
    ReactionClass.FIRST_SESSION: (
        FeedbackTemplate(
            key="first.saved",
            reaction=ReactionClass.FIRST_SESSION,
            text="Записал. Это первый эпизод — новый ориентир: {next_target}.",
        ),
        FeedbackTemplate(
            key="first.started",
            reaction=ReactionClass.FIRST_SESSION,
            text="Есть, сохранил. Отсюда начинаем интервал до {next_target}.",
        ),
    ),
    ReactionClass.EARLY: (
        FeedbackTemplate(
            key="early.continue",
            reaction=ReactionClass.EARLY,
            text=(
                "Записал. До рубежа не хватило {earliness} — ничего страшного, "
                "продолжаем. Новый ориентир: {next_target}."
            ),
        ),
        FeedbackTemplate(
            key="early.next",
            reaction=ReactionClass.EARLY,
            text=(
                "Сохранил: на {earliness} раньше ориентира. Спокойно идём дальше; "
                "следующий рубеж — {next_target}."
            ),
        ),
    ),
    ReactionClass.ON_SCHEDULE: (
        FeedbackTemplate(
            key="on_schedule.cool",
            reaction=ReactionClass.ON_SCHEDULE,
            text="Круто, интервал выдержан! Следующий ориентир: {next_target}.",
        ),
        FeedbackTemplate(
            key="on_schedule.great",
            reaction=ReactionClass.ON_SCHEDULE,
            text="Отлично держишь режим. Новый ориентир: {next_target}.",
        ),
    ),
    ReactionClass.SIGNIFICANTLY_LATE: (
        FeedbackTemplate(
            key="late.very_cool",
            reaction=ReactionClass.SIGNIFICANTLY_LATE,
            text=("Вообще круто — ещё {lateness} сверх рубежа! Новый ориентир: {next_target}."),
        ),
        FeedbackTemplate(
            key="late.strong",
            reaction=ReactionClass.SIGNIFICANTLY_LATE,
            text=(
                "Сильный результат: ты продержался на {lateness} дольше. "
                "Следующий ориентир: {next_target}."
            ),
        ),
    ),
}


def choose_feedback_template(
    reaction: ReactionClass,
    *,
    previous_template_key: str | None,
    chooser: TemplateIndexChooser,
) -> FeedbackTemplate:
    """Choose within the correct class while excluding the previous key."""
    templates = FEEDBACK_TEMPLATES[reaction]
    candidates = tuple(template for template in templates if template.key != previous_template_key)
    if not candidates:
        candidates = templates
    selected_index = chooser.choose(len(candidates))
    if not 0 <= selected_index < len(candidates):
        raise ValueError("Template chooser returned an out-of-range index")
    return candidates[selected_index]


def render_feedback(
    template: FeedbackTemplate,
    facts: FeedbackFacts,
    *,
    format_instant: Callable[[UtcInstant], str],
    format_duration: Callable[[timedelta], str],
) -> str:
    """Interpolate only facts already calculated by the interval policy."""
    if template.reaction is not facts.reaction:
        raise ValueError("Feedback template does not match the fact reaction")
    return template.text.format(
        next_target=format_instant(facts.next_target_at),
        earliness=format_duration(facts.earliness),
        lateness=format_duration(facts.lateness),
    )
