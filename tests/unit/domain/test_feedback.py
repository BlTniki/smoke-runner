"""Tests for supportive, non-repeating feedback selection."""

from dataclasses import dataclass
from datetime import timedelta

import pytest

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.feedback import (
    FEEDBACK_TEMPLATES,
    FeedbackFacts,
    choose_feedback_template,
    render_feedback,
)
from smoke_runner.domain.interval_policy import ReactionClass


@dataclass
class FixedChooser:
    index: int

    def choose(self, candidate_count: int) -> int:
        del candidate_count
        return self.index


@pytest.mark.parametrize("reaction", list(ReactionClass))
def test_every_reaction_has_multiple_safe_templates(reaction: ReactionClass) -> None:
    templates = FEEDBACK_TEMPLATES[reaction]

    assert len(templates) >= 2
    assert all(template.reaction is reaction for template in templates)
    combined_text = " ".join(template.text.lower() for template in templates)
    assert "сорвался" not in combined_text
    assert "провал" not in combined_text


def test_previous_template_is_excluded_from_candidates() -> None:
    first = FEEDBACK_TEMPLATES[ReactionClass.ON_SCHEDULE][0]

    chosen = choose_feedback_template(
        ReactionClass.ON_SCHEDULE,
        previous_template_key=first.key,
        chooser=FixedChooser(0),
    )

    assert chosen.key != first.key


def test_bad_chooser_index_is_rejected() -> None:
    with pytest.raises(ValueError):
        choose_feedback_template(
            ReactionClass.EARLY,
            previous_template_key=None,
            chooser=FixedChooser(99),
        )


def test_feedback_renderer_only_interpolates_precalculated_facts() -> None:
    facts = FeedbackFacts(
        reaction=ReactionClass.EARLY,
        target_at=UtcInstant.from_unix_seconds(100),
        next_target_at=UtcInstant.from_unix_seconds(200),
        earliness=timedelta(minutes=18),
        lateness=timedelta(0),
    )
    template = FEEDBACK_TEMPLATES[ReactionClass.EARLY][0]

    rendered = render_feedback(
        template,
        facts,
        format_instant=lambda value: f"utc:{value.to_unix_seconds()}",
        format_duration=lambda value: f"{int(value.total_seconds() // 60)} минут",
    )

    assert "18 минут" in rendered
    assert "utc:200" in rendered


def test_feedback_renderer_rejects_template_from_another_reaction() -> None:
    facts = FeedbackFacts(
        reaction=ReactionClass.EARLY,
        target_at=UtcInstant.from_unix_seconds(100),
        next_target_at=UtcInstant.from_unix_seconds(200),
        earliness=timedelta(minutes=1),
        lateness=timedelta(0),
    )

    with pytest.raises(ValueError):
        render_feedback(
            FEEDBACK_TEMPLATES[ReactionClass.ON_SCHEDULE][0],
            facts,
            format_instant=str,
            format_duration=str,
        )
