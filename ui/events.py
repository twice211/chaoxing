"""Scoped desktop events preserve the legacy two-value queue interface."""
from __future__ import annotations


class WorkerEvent(tuple[str, object]):
    course_id: int | None
    request_id: str | None

    def __new__(cls, level: str, payload: object, *, course_id: int | None = None,
                request_id: str | None = None) -> WorkerEvent:
        event = super().__new__(cls, (level, payload))
        event.course_id = course_id
        event.request_id = request_id
        return event


COURSE_ACTIONS = frozenset({
    "select_course", "catalog", "open_item", "play", "pause", "resume", "stop_play",
    "auto_next", "task_tab", "parse", "auto", "auto_chain", "stop", "grades", "read",
    "dump", "wrong_list", "search",
})
COURSE_RESULTS = frozenset({
    "raw", "ai", "grades", "discuss_post", "discuss_reply", "discuss_rec", "discuss_confirm",
    "search_results", "wrong_results", "playstatus", "item", "row", "sections", "progress",
})


def is_course_action(action: str) -> bool:
    return action in COURSE_ACTIONS or action.startswith("discuss_")
