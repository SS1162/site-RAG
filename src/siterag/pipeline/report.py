"""Markdown run report: ``data/<site_id>/reports/run_<UTC timestamp>.md``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from siterag.domain.entities import PageEvent, Run
from siterag.domain.enums import EventType
from siterag.pipeline.frontier import DiscoveryStats
from siterag.ports.fetcher import FetchStats

COUNTERS = (
    ("new", "pages_new"),
    ("changed", "pages_changed"),
    ("unchanged", "pages_unchanged"),
    ("removed", "pages_removed"),
    ("redirected", "pages_redirected"),
    ("skipped", "pages_skipped"),
    ("orphaned", "pages_orphaned"),
    ("failed", "pages_failed"),
)


def report_path(reports_dir: Path, run: Run) -> Path:
    """File name from the run's start time in UTC."""
    return reports_dir / f"run_{run.started_at.strftime('%Y%m%dT%H%M%SZ')}.md"


def latest_report(reports_dir: Path) -> Path | None:
    """Most recent report file, or None."""
    files = sorted(reports_dir.glob("run_*.md"))
    return files[-1] if files else None


def _details(event: PageEvent) -> str:
    d = event.details
    if event.event == EventType.REMOVED:
        if "reason" in d:
            return f"{d['reason']} ({d['rule']})" if d.get("rule") else str(d["reason"])
        return f"HTTP {d.get('http_status', '?')}"
    if event.event == EventType.SKIPPED:
        extra = f" → {d['target']}" if d.get("target") else ""
        return f"{d.get('reason', '?')}{extra}"
    if event.event in (EventType.FAILED, EventType.FAILED_PERMANENT):
        suffix = " (permanent)" if event.event == EventType.FAILED_PERMANENT else ""
        return f"{d.get('error', '?')}{suffix}"
    if event.event == EventType.ORPHAN:
        return "content changed" if d.get("changed") else "unchanged"
    return ""


def _event_section(
    title: str,
    events: Sequence[PageEvent],
    types: set[EventType],
    urls: Mapping[str, str],
) -> list[str]:
    selected = [e for e in events if e.event in types]
    lines = [f"## {title} ({len(selected)})", ""]
    if not selected:
        return [*lines, "None.", ""]
    lines += ["| URL | Details |", "|---|---|"]
    lines += [f"| {urls.get(e.url_id, e.url_id)} | {_details(e)} |" for e in selected]
    return [*lines, ""]


def render_report(
    run: Run,
    events: Sequence[PageEvent],
    urls: Mapping[str, str],
    fetch_stats: FetchStats,
    discovery: DiscoveryStats,
    warnings: Sequence[str],
) -> str:
    """Render the report. ``urls`` maps url_id → display URL for the listed events."""
    duration = (
        f"{(run.finished_at - run.started_at).total_seconds():.0f}s" if run.finished_at else "-"
    )
    pct_304 = (
        100 * fetch_stats.not_modified / fetch_stats.requests if fetch_stats.requests else 0.0
    )
    lines = [
        f"# Run report — {run.site_id}",
        "",
        f"- Run: `{run.run_id}` ({run.command} {run.args})",
        f"- Status: **{run.status.value}**"
        + (f" — {run.abort_reason}" if run.abort_reason else ""),
        f"- Started: {run.started_at.isoformat()}",
        f"- Finished: {run.finished_at.isoformat() if run.finished_at else '-'}",
        f"- Duration: {duration}",
        "",
        "## Counters",
        "",
        "| " + " | ".join(name for name, _ in COUNTERS) + " |",
        "|" + "---|" * len(COUNTERS),
        "| " + " | ".join(str(getattr(run, attr)) for _, attr in COUNTERS) + " |",
        "",
        "## Requests",
        "",
        f"- HTTP responses: {fetch_stats.requests}",
        f"- 304 Not Modified: {fetch_stats.not_modified} ({pct_304:.1f}%)",
        f"- Average response time: {fetch_stats.avg_response_s:.3f}s",
        "",
        "## Discovery",
        "",
        f"- URLs admitted: {discovery.discovered}",
        f"- Blocked by robots.txt: {discovery.robots_blocked}",
        f"- Too deep: {discovery.too_deep}; over max_pages: {discovery.over_cap}; "
        f"other host: {discovery.offsite}; invalid: {discovery.invalid}",
    ]
    for rule, count in discovery.excluded_by_rule.most_common():
        lines.append(f"- Excluded by `{rule}`: {count}")
    lines += ["", f"## Warnings ({len(warnings)})", ""]
    lines += [f"- {w}" for w in warnings] or ["None."]
    lines.append("")
    lines += _event_section("Failures", events, {EventType.FAILED, EventType.FAILED_PERMANENT},
                            urls)
    lines += _event_section("Skipped", events, {EventType.SKIPPED}, urls)
    lines += _event_section("Orphans", events, {EventType.ORPHAN}, urls)
    lines += _event_section("Removals", events, {EventType.REMOVED}, urls)
    return "\n".join(lines)


LISTED_EVENTS = frozenset(
    {
        EventType.FAILED,
        EventType.FAILED_PERMANENT,
        EventType.SKIPPED,
        EventType.ORPHAN,
        EventType.REMOVED,
    }
)
"""Events listed one by one in the report (others are only counted)."""
