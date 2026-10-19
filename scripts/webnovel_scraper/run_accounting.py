"""Page and format accounting from journal evidence, separate from story authenticity."""
from __future__ import annotations

from collections import Counter


def page_accounting(count, formats, attempts, contents, failures, publishes, *, explicit=True):
    """No displayed-number arithmetic: all memberships use immutable selection positions.

    Known failures are retained as history; a later content observation or successful
    format publication can resolve their current outcome. Missing events in older
    journals remain unrecorded, rather than proof a page was never attempted.
    """
    held = {}
    for record in publishes.values():
        for position in record["pages"]:
            held.setdefault(position, {})[record["format"]] = record
    latest = {}
    failed_positions = set()
    for record in failures:
        latest[(record["position"], record.get("format"))] = record
        failed_positions.add(record["position"])
    rows, totals, by_format = [], Counter(), {fmt: Counter() for fmt in formats}
    for position in range(count):
        body = contents.get(position)
        fetch = latest.get((position, None))
        if fetch and body and body["seq"] > fetch["seq"]:
            fetch = None
        available = held.get(position, {})
        recorded = position in attempts or body is not None or position in failed_positions or bool(available)
        processed = bool(body or position in failed_positions or available)
        states = {}
        for fmt in formats:
            published = available.get(fmt)
            failure = latest.get((position, fmt)) or fetch
            if published and (failure is None or failure.get("stage", "fetch") == "fetch" or published["seq"] > failure["seq"]):
                state = "success"
            elif failure:
                state = "failure"
            elif recorded:
                state = "pending"
            else:
                state = "not_attempted" if explicit else "unrecorded"
            states[fmt] = state
            by_format[fmt][state] += 1
        fallback = sorted(fmt for fmt, record in available.items() if record.get("fallback_for"))
        success = all(s == "success" for s in states.values())
        failed = not success and any(s == "failure" for s in states.values())
        totals["attempted"] += recorded
        totals["processed"] += processed
        totals["fetch_failures"] += fetch is not None
        totals["requested_success"] += success
        totals["requested_failure"] += failed
        totals["fallback_preserved"] += bool(fallback)
        totals["not_attempted"] += not recorded and explicit
        totals["unrecorded"] += not recorded and not explicit
        totals["pending"] += recorded and not success and not failed
        totals["integrity_suspicions"] += bool(body and body.get("duplicate_of") is not None)
        rows.append(dict(position=position, attempted=recorded,
                         processed=processed, requested_format_outcomes=states,
                         fallback_formats=fallback, failure=fetch,
                         content=body, outputs=available))
    return rows, dict(selected=count, **totals,
                      formats={fmt: dict(states) for fmt, states in by_format.items()},
                      actual_artifacts=dict(Counter(r["format"] for r in publishes.values())),
                      attempt_tracking="explicit" if explicit else "legacy-inferred")
