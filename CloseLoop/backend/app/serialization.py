"""Shared serialization helpers.

Several routers keep their own ``_serialize_subtask`` (tasks, subtasks,
plans, dashboards). These helpers let each one populate the new
multi-assignee fields without every read path having to eager-load the
``SubTask.entity_assignees`` relationship — accessing an unloaded async
relationship raises, so we check the loaded state first and return empty
otherwise.
"""

from __future__ import annotations

from sqlalchemy import inspect as _sa_inspect

from .schemas import SubTaskEntityAssigneeOut, UserMini


def _entity_assignees_loaded(s) -> bool:
    try:
        return "entity_assignees" not in _sa_inspect(s).unloaded
    except Exception:  # pragma: no cover - detached / odd states
        return False


def subtask_entity_assignees_out(s, users: dict[str, UserMini]) -> list[SubTaskEntityAssigneeOut]:
    """SubTaskEntityAssigneeOut list for a SubTask — [] if the relationship
    wasn't eager-loaded (never triggers a lazy load)."""
    if not _entity_assignees_loaded(s):
        return []
    return [
        SubTaskEntityAssigneeOut(
            id=a.id,
            subtask_id=a.subtask_id,
            user_id=a.user_id,
            done=a.done,
            done_at=a.done_at,
            due_date=a.due_date,
            completion_notes=a.completion_notes,
            completion_time_minutes=a.completion_time_minutes,
            created_at=a.created_at,
            user=users.get(a.user_id),
        )
        for a in s.entity_assignees
    ]


def subtask_entity_assignee_user_ids(s) -> set[str]:
    """User ids on a SubTask's multi-assignee rows — {} if not eager-loaded."""
    if not _entity_assignees_loaded(s):
        return set()
    return {a.user_id for a in s.entity_assignees if a.user_id}
