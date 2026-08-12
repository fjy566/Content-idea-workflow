"""Shared rules for deciding whether a topic has already been handled."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from .models import Feedback, Topic


# A topic leaves the default recommendation queue after any meaningful user
# interaction.  ``view`` is intentional: opening the detail page means the
# user has already received this topic as an idea and should see another one
# next time.  Articles are checked separately so manually-created or legacy
# articles without a feedback row are also handled correctly.
HANDLED_FEEDBACK_ACTIONS = frozenset(
    {
        "view",
        "save",
        "dismiss",
        "generate",
        "choose_angle",
        "add_image",
        "edit",
        "publish",
    }
)


def handled_topic_expression() -> ColumnElement[bool]:
    return or_(
        Topic.feedback.any(Feedback.action.in_(tuple(HANDLED_FEEDBACK_ACTIONS))),
        Topic.articles.any(),
    )


def unhandled_topic_expression() -> ColumnElement[bool]:
    return ~handled_topic_expression()


def is_topic_handled(topic: Topic) -> bool:
    return bool(topic.articles) or any(
        feedback.action in HANDLED_FEEDBACK_ACTIONS for feedback in topic.feedback
    )
