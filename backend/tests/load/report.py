"""Markdown report: one section per load test, upserted into docs/reviews/load-<date>.md.

Each test builds its checks (`latency_checks`), writes its section, then asserts `all_ok`, so a
failing run still leaves its numbers in the report.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tests.load import scenarios as sc
from tests.load.driver import RunResult, pct
from tests.load.synthetic_day import PROVIDER_BASE

OD19 = "OD-19 candidate: parallel interpret, serial apply (TRD §2.3); not Kafka/Redis"


@dataclass(frozen=True)
class Check:
    metric: str
    value: str
    threshold: str  # "" for informational rows
    ok: bool | None  # None: informational


def le(metric: str, value: float, limit: float, unit: str, *, strict: bool = False) -> Check:
    ok = value < limit if strict else value <= limit
    return Check(metric, f"{value:.3f} {unit}", f"{'<' if strict else '<='} {limit:g} {unit}", ok)


def info(metric: str, value: str) -> Check:
    return Check(metric, value, "", None)


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok is not False for c in checks)


def latency_checks(res: RunResult, *, queue_wait_asserted: bool) -> list[Check]:
    """The TRD §12 pass criteria for one run. Queue wait is a criterion for the cross-provider
    burst (test 2); elsewhere it is reported but does not decide pass/fail."""
    worst = res.worst_queue_wait_p95_s()
    queue = (
        le("queue wait p95, worst provider", worst, sc.QUEUE_WAIT_P95_S, "s")
        if queue_wait_asserted
        else info("queue wait p95, worst provider (informational)", f"{worst:.3f} s")
    )
    lost = res.sent - res.accepted + res.unfinished
    return [
        info("messages sent / accepted", f"{res.sent} / {res.accepted}"),
        info("end-to-end p50", f"{pct(res.e2e_s, 50):.3f} s"),
        le("end-to-end p95", pct(res.e2e_s, 95), sc.E2E_P95_S, "s"),
        le("end-to-end p99", pct(res.e2e_s, 99), sc.E2E_P99_S, "s"),
        info("end-to-end max", f"{max(res.e2e_s, default=0.0):.3f} s"),
        queue,
        info(
            "inbox insert p50 / p95 / max",
            f"{pct(res.insert_ms, 50):.1f} / {pct(res.insert_ms, 95):.1f} / "
            f"{max(res.insert_ms, default=0.0):.1f} ms",
        ),
        le("inbox insert p99", pct(res.insert_ms, 99), sc.INSERT_P99_MS, "ms", strict=True),
        info(
            "event-loop lag p99 / max (diagnostic)",
            f"{pct(res.loop_lag_ms, 99):.1f} / {max(res.loop_lag_ms, default=0.0):.1f} ms",
        ),
        Check("failed / poisoned messages", str(res.failed), "== 0", res.failed == 0),
        Check("processing exceptions (retries)", str(len(res.errors)), "== 0", not res.errors),
        Check("lost (not accepted or never finished)", str(lost), "== 0", lost == 0),
    ]


def render_checks(checks: list[Check]) -> str:
    rows = ["| Metric | Value | Threshold | Result |", "|---|---|---|---|"]
    for c in checks:
        result = "info" if c.ok is None else ("PASS" if c.ok else "**FAIL**")
        rows.append(f"| {c.metric} | {c.value} | {c.threshold or '-'} | {result} |")
    return "\n".join(rows)


def render_details(res: RunResult) -> str:
    lines = [
        f"Wall time {res.wall_s:.1f} s. Kinds: "
        + ", ".join(f"{k} {v}" for k, v in sorted(res.kinds.items())),
        f"Interpreter (signals): {res.interpreters}. Model call outcomes: {res.model_outcomes}. "
        f"Fake calls: jev {res.jev_calls}, llm {res.llm_calls}. "
        f"Inbox statuses: {res.statuses}. Cards (action/state) {res.cards}, "
        f"`pushed` stamps {res.pushed} (cards {sum(res.cards.values())}).",
        "",
        "| Path | n | p50 s | p95 s | p99 s |",
        "|---|---|---|---|---|",
    ]
    for path, values in sorted(res.e2e_by_path.items()):
        lines.append(
            f"| {path} | {len(values)} | {pct(values, 50):.3f} | {pct(values, 95):.3f} "
            f"| {pct(values, 99):.3f} |"
        )
    waits = res.queue_wait_p95_s()
    if waits:
        lines += [
            "",
            "Per-provider queue wait p95 (s): "
            + ", ".join(
                f"p{pid - PROVIDER_BASE}: {w:.3f} ({res.slow_by_provider.get(pid, 0)} slow)"
                for pid, w in waits.items()
            ),
        ]
    return "\n".join(lines)


def render_section(
    title: str, checks: list[Check], details: str = "", notes: tuple[str, ...] = ()
) -> str:
    verdict = "PASS" if all_ok(checks) else "FAIL"
    parts = [f"## {title}: {verdict}", "", render_checks(checks)]
    if details:
        parts += ["", details]
    parts += ["", *(f"- {n}" for n in notes)] if notes else []
    return "\n".join(parts).rstrip() + "\n"


def report_path(today: datetime | None = None) -> Path:
    directory = os.environ.get("LOAD_REPORT_DIR")
    base = Path(directory) if directory else Path(__file__).parents[3] / "docs" / "reviews"
    return base / f"load-{(today or datetime.now(UTC)).date().isoformat()}.md"


def header(today: datetime | None = None) -> str:
    stamp = (today or datetime.now(UTC)).date().isoformat()
    jev, llm = sc.latencies()
    return (
        f"# Load and fault report, {stamp}\n\n"
        "Generated by `cd backend && uv run pytest tests/load -m load` (see "
        "`backend/tests/load/README.md`). Model latency is **simulated** from seeded lognormals "
        f"(placeholders until F048 bench numbers are exported): Jev median {jev.median_ms:g} ms / "
        f"p95 {jev.p95_ms:g} ms, LLM extract median {llm.median_ms:g} ms / p95 {llm.p95_ms:g} ms. "
        f"Seed {sc.SEED}; day = {sc.DAY_MESSAGES} messages over {sc.DAY_SPAN_S:g} s, "
        f"{sc.SLOW_PCT}% of actionable messages take the slow path. Pass criteria: end-to-end "
        f"p95 <= {sc.E2E_P95_S:g} s, p99 <= {sc.E2E_P99_S:g} s; queue wait p95 <= "
        f"{sc.QUEUE_WAIT_P95_S:g} s; inbox insert p99 < {sc.INSERT_P99_MS:g} ms; zero lost or "
        "duplicated effects.\n"
    )


def upsert(key: str, section: str, path: Path | None = None) -> Path:
    """Replace (or append) the section `key` in the report file, creating file and directory."""
    path = path or report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else header()
    start, end = f"<!-- load:{key} -->", f"<!-- /load:{key} -->"
    block = f"{start}\n{section.rstrip()}\n{end}\n"
    if start in text and end in text:
        head, rest = text.split(start, 1)
        text = head + block + rest.split(end, 1)[1].lstrip("\n")
    else:
        text = text.rstrip() + "\n\n" + block
    path.write_text(text)
    return path
