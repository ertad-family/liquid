"""Benchmark runner — executes every task and emits a markdown report.

    python -m benchmarks.run              # run all, write RESULTS.md
    python -m benchmarks.run --no-write   # print only, skip the file
    python -m benchmarks.run --only 1,2,6 # run a subset

Numbers come from actual mocked HTTP runs; nothing here is fabricated.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import liquid
from benchmarks.harness import TaskResult
from benchmarks.tasks import ALL_TASKS

RESULTS_PATH = Path(__file__).parent / "RESULTS.md"


def _fmt_number(value: float, unit: str) -> str:
    if unit in ("tokens", "bytes", "items", "pages", "fields"):
        return f"{int(value):,}"
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "bool":
        return "yes" if value else "no"
    return f"{value:.2f}"


def render_summary(results: list[TaskResult]) -> str:
    lines = [
        "| # | Task | Metric | Baseline | Liquid | Delta |",
        "|---|------|--------|---------:|-------:|------:|",
    ]
    for r in results:
        m = r.primary()
        lines.append(
            f"| {r.task_id.split('_')[-1]} | {r.title} | {m.unit} "
            f"| {_fmt_number(m.baseline, m.unit)} "
            f"| {_fmt_number(m.liquid, m.unit)} "
            f"| {m.fmt_delta()} |"
        )
    return "\n".join(lines)


def render_detail(result: TaskResult) -> str:
    lines = [f"### {result.task_id}: {result.title}", "", result.notes, ""]
    lines.append("| Metric | Baseline | Liquid | Delta |")
    lines.append("|--------|---------:|-------:|------:|")
    for m in result.measurements:
        lines.append(
            f"| {m.unit} | {_fmt_number(m.baseline, m.unit)} | {_fmt_number(m.liquid, m.unit)} | {m.fmt_delta()} |"
        )
    if result.details:
        lines.append("")
        lines.append("Details:")
        lines.append("")
        lines.append("```json")
        import json

        lines.append(json.dumps(result.details, indent=2, default=str))
        lines.append("```")
    return "\n".join(lines)


def render_report(results: list[TaskResult]) -> str:
    header = [
        "# Liquid benchmark results",
        "",
        f"- **liquid version**: {liquid.__version__}",
        f"- **date (UTC)**: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- **python**: {platform.python_version()}",
        f"- **platform**: {platform.system()} {platform.release()}",
        "",
        "## Summary",
        "",
        render_summary(results),
        "",
        "## Per-task detail",
        "",
    ]
    body = []
    for r in results:
        body.append(render_detail(r))
        body.append("")

    footer = [
        "## Methodology",
        "",
        "- HTTP is mocked via ``httpx.MockTransport`` — no real APIs are hit.",
        "- Fixtures (500 orders, 200 tickets, 1 fat customer, 1 Stripe charge, "
        "1 PayPal payment) are generated deterministically from a fixed seed.",
        "- Token counts use the same formula as Liquid's internal estimator: ``len(json.dumps(payload)) // 4``.",
        "- Each task runs **baseline** (no Liquid features) and **liquid** (with the "
        "relevant feature) against the **same** mock data.",
        "- Pages fetched counts HTTP requests made by the transport — an "
        "indicator of wire-level cost, not wall-clock latency.",
        "",
        "Reproduce:",
        "",
        "```bash",
        "uv venv .venv && source .venv/bin/activate",
        "uv pip install -e '.[dev]'",
        "python -m benchmarks.fixtures._generate   # regenerate fixtures (deterministic)",
        "python -m benchmarks.run",
        "```",
        "",
    ]
    return "\n".join(header + body + footer)


async def run_tasks(selected: set[int] | None = None) -> list[TaskResult]:
    results: list[TaskResult] = []
    for idx, task_fn in enumerate(ALL_TASKS, start=1):
        if selected is not None and idx not in selected:
            continue
        try:
            result = await task_fn()
        except Exception as exc:  # keep running other tasks
            results.append(
                TaskResult(
                    task_id=f"task_{idx:02d}",
                    title=f"(failed: {task_fn.__module__})",
                    metric="error",
                    notes=f"Exception during run: {exc!r}",
                )
            )
            continue
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Liquid agent benchmarks.")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report but don't update RESULTS.md",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated task numbers to run (e.g. 1,3,6).",
    )
    args = parser.parse_args(argv)

    selected: set[int] | None = None
    if args.only:
        selected = {int(x) for x in args.only.split(",") if x.strip()}

    results = asyncio.run(run_tasks(selected))
    report = render_report(results)
    print(report)
    if not args.no_write:
        RESULTS_PATH.write_text(report)
        print(f"\nWrote {RESULTS_PATH}", file=sys.stderr)
    # Return non-zero if any task errored, so CI can pick it up.
    return 1 if any(r.metric == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
