"""Raw thread-state signal accessors."""

from __future__ import annotations


def get_thread_state_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("thread_state", []) or [])
