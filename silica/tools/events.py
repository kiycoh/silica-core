# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""User-event tools: create, update, and the 4-axis agenda.

Events are vault notes identified by `event_start` frontmatter (ADR-0024);
these tools are the chat/MCP surface over the pure kernel lane
`silica/kernel/calendar/`. Writes follow the sibling single-note tools
(notes.py): strict kernel validation first, then DRIVER under path_lease
with the system floor stamped and a checkpoint pushed for /undo.
"""
from __future__ import annotations

from typing import Annotated

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from silica.driver import DRIVER
from silica.tools import tool

logger = logging.getLogger(__name__)

_FIELD_TO_KEY = {
    "start": "event_start",
    "end": "event_end",
    "rrule": "event_rrule",
    "reminder": "event_reminder",
    "status": "event_status",
}


def _vault() -> Path:
    from silica.config import CONFIG
    return Path(CONFIG.vault_path)


@tool(cls="composed", collapse="eager")
def silica_event_create(title: Annotated[str, Field(description='Event title')], start: Annotated[str, Field(description="'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD' = all-day")], end: Annotated[str, Field(description='Optional end; all-day end date is inclusive')] = "", rrule: Annotated[str, Field(description="iCal RRULE, e.g. 'FREQ=WEEKLY;BYDAY=WE'")] = "",
                        reminder: Annotated[str, Field(description='Lead before each occurrence: <N>m|h|d')] = "", body: Annotated[str, Field(description='Optional markdown body')] = "") -> dict[str, Any]:
    """Create a user event (appointment, deadline) as a calendar note.
    Revertible with /undo."""
    from silica.kernel.calendar.model import validate_event
    from silica.kernel.vault_manifest import in_write_dir
    from silica.kernel.workqueue import path_lease
    from silica.kernel.write import templates as tpl
    from silica.kernel.write.checkpoints import get_checkpoint_store

    title = title.strip()
    if not title:
        return {"error": "title is required"}
    fm: dict[str, Any] = {"event_start": start}
    if end:
        fm["event_end"] = end
    if rrule:
        fm["event_rrule"] = rrule
    if reminder:
        fm["event_reminder"] = reminder
    errors = validate_event(fm)
    if errors:
        return {"error": "; ".join(errors)}

    # Recurring events keep a date-free stem: the series has no single day.
    # slugify strips every path separator, so the stem cannot steer the write out
    # of the calendar folder — but a recurring title leads the stem, and every
    # reader of the calendar (scan_events, the timeline walk, ignore_matcher)
    # skips a path part starting with "." as vault plumbing. A leading dot would
    # therefore write the note, report success, and leave an event no agenda,
    # update or reminder can ever see again. A title of nothing but dots and
    # stripped characters has no filename left at all.
    safe_title = tpl.slugify(title).lstrip(".")
    if not safe_title:
        return {"error": f"title {title!r} has no usable characters for a filename"}
    stem = safe_title if rrule else f"{start.strip()[:10]} {safe_title}"
    folder = in_write_dir("calendar")

    ordered: dict[str, Any] = {"title": title, **fm}
    ordered["last modified"] = dt.date.today().isoformat()
    block = yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False).strip()
    content = tpl.ensure_system_floor(
        f"---\n{block}\n---\n\n{body.strip()}\n" if body.strip()
        else f"---\n{block}\n---\n")

    with path_lease(f"{folder}/{stem}.md"):
        path = f"{folder}/{stem}.md"
        n = 2
        while True:
            try:
                DRIVER.read_note(path)
            except Exception:
                break  # free slot — the happy path
            path = f"{folder}/{stem} {n}.md"
            n += 1
        try:
            ref = DRIVER.create(path, content)
        except Exception as e:
            return {"error": f"Failed to create event note '{path}': {e}"}

        checkpoint_depth = None
        checkpoint_ok = False
        try:
            checkpoint_depth = get_checkpoint_store().push(path, "", content)
            checkpoint_ok = True
        except Exception as e:
            logger.warning("checkpoint push failed for '%s': %s — /undo has no "
                           "restore point for this write", path, e)

    return {"op": "write", "success": True, "path": ref.path or path,
            "checkpoint_depth": checkpoint_depth, "checkpoint_ok": checkpoint_ok}


@tool(cls="composed", collapse="eager")
def silica_event_update(note: Annotated[str, Field(description='Event note stem or path')], start: Annotated[str, Field(description='New start (empty = keep)')] = "", end: Annotated[str, Field(description='New end')] = "", rrule: Annotated[str, Field(description='New RRULE')] = "",
                        reminder: Annotated[str, Field(description='New lead (<N>m|h|d)')] = "", status: Annotated[str, Field(description='done|cancelled closes the series')] = "") -> dict[str, Any]:
    """Update an event note: only the passed fields change; the merged set
    is re-validated first. status=done|cancelled closes the series."""
    from silica.kernel.calendar import reminders as rem
    from silica.kernel.calendar.model import scan_events, validate_event
    from silica.kernel.workqueue import path_lease
    from silica.kernel.write import frontmatter
    from silica.kernel.write.checkpoints import get_checkpoint_store
    from silica.kernel.write.templates import upsert_props

    changes = {k: v.strip() for k, v in
               dict(start=start, end=end, rrule=rrule, reminder=reminder,
                    status=status).items() if v.strip()}
    if not changes:
        return {"error": "nothing to update: pass at least one field"}

    vault = _vault()
    target = None
    for e in scan_events(vault):
        if e.stem == note or e.path == note or e.path == f"{note}.md":
            target = e
            break
    if target is None:
        return {"error": f"No event note found for '{note}'"}

    with path_lease(target.path):
        try:
            prior = DRIVER.read_note(target.path).content
        except Exception as e:
            return {"error": f"Failed to read '{target.path}': {e}"}
        data, _, _ = frontmatter.split(prior)
        merged = dict(data or {})
        merged.update({_FIELD_TO_KEY[k]: v for k, v in changes.items()})
        errors = validate_event(merged)
        if errors:
            return {"error": "; ".join(errors)}

        new_content = upsert_props(prior, {_FIELD_TO_KEY[k]: v for k, v in changes.items()})
        try:
            DRIVER.overwrite(target.path, new_content)
        except Exception as e:
            return {"error": f"Failed to update '{target.path}': {e}"}

        checkpoint_ok = False
        try:
            get_checkpoint_store().push(target.path, prior, new_content)
            checkpoint_ok = True
        except Exception as e:
            logger.warning("checkpoint push failed for '%s': %s", target.path, e)

    if "start" in changes or "rrule" in changes:
        # An event moved earlier than its mark would otherwise never remind.
        with rem.delivery_lock(vault):
            marks = rem.load_marks(vault)
            if target.stem in marks:
                del marks[target.stem]
                rem.save_marks(vault, marks)

    return {"op": "update", "success": True, "path": target.path,
            "changed": sorted(changes), "checkpoint_ok": checkpoint_ok}


@tool(cls="composed")
def silica_agenda(start: Annotated[str, Field(description="Window start: 'today' or 'YYYY-MM-DD'")] = "today", days: Annotated[int, Field(ge=1, le=90, description='Window length in days')] = 7) -> dict[str, Any]:
    """Per-day agenda: event occurrences, dated notes, agent activity, and
    review-due (today only)."""
    from silica.kernel.calendar.agenda import agenda
    from silica.kernel.calendar.model import scan_events
    from silica.kernel.calendar.occurrences import expand
    from silica.kernel.write.timeline import timeline

    today = dt.date.today()
    s = (start or "today").strip().casefold()
    if s in ("", "today"):
        start_date = today
    else:
        try:
            start_date = dt.date.fromisoformat(s)
        except ValueError:
            return {"error": f"start must be 'today' or YYYY-MM-DD, got {start!r}"}
    end_date = start_date + dt.timedelta(days=days)
    win_start = dt.datetime.combine(start_date, dt.time())
    win_end = dt.datetime.combine(end_date, dt.time())
    now = dt.datetime.now()

    vault = _vault()
    events = scan_events(vault)
    occurrences = [o for e in events for o in expand(e, win_start, win_end, now=now)]

    tl = timeline(vault, start=start_date.isoformat(),
                  end=(end_date - dt.timedelta(days=1)).isoformat(), limit=1000)["rows"]

    log_lines: list[str] = []
    try:
        from silica.kernel.recall.run_log import tail_log
        log_lines = tail_log(1000, vault_path=str(vault))
    except OSError:
        pass  # no journal yet

    review_rows: list[dict] = []
    try:
        from silica.kernel.report import learner
        event_paths = {e.path for e in events}
        # Event notes are schedule, not study material: on a fresh vault the
        # picker marks every note unexplored and today's row would flood with
        # the calendar itself. Same exclusion the dated-notes axis has.
        review_rows = [r for r in learner.review_queue(limit=10)
                       if r.get("path") not in event_paths]
    except Exception as e:
        logger.debug("agenda: review axis unavailable (%s)", e)

    rows = agenda(start_date, end_date, occurrences=occurrences,
                  timeline_rows=tl, log_lines=log_lines, review_rows=review_rows,
                  event_stems={ev.stem for ev in events}, today=today)

    lines: list[str] = [f"Agenda {start_date.isoformat()} +{days}d"]
    for r in rows:
        content = []
        for evt in r["events"]:
            when = "all-day" if evt["all_day"] else evt["start"][11:]
            mark = f" [{evt['status']}]" if evt["status"] else ""
            content.append(f"  {when}  {evt['title']}{mark}")
        if r["notes"]:
            content.append("  notes: " + ", ".join(n["label"] for n in r["notes"]))
        for a in r["activity"]:
            content.append(f"  agent: {a}")
        if r["review"]:
            content.append("  review due: " + ", ".join(
                str(x.get("path", "")) for x in r["review"]))
        if content:
            lines.append(f"{r['date']}:")
            lines.extend(content)
    if len(lines) == 1:
        lines.append("(empty window)")

    return {"start": start_date.isoformat(), "days": rows, "text": "\n".join(lines)}
