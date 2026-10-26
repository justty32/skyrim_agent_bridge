"""QA report 文字輸出。"""

from __future__ import annotations


from qa_runner_common import (  # noqa: F401
    FAIL, HANDOFF, PASS, SKIPPED,
)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

MARK = {PASS: "  ok  ", FAIL: " FAIL ", HANDOFF: " look ", SKIPPED: " skip "}


def render(report: dict) -> str:
    lines = [f"{report['name']} — {report['status'].upper()} in {report['duration_s']}s"]
    for step in report["steps"]:
        tail = f" ({step['duration_s']}s)" if step["duration_s"] else ""
        phase = " [teardown]" if step.get("phase") == "teardown" else ""
        lines.append(f"[{MARK[step['status']]}] {step['label']}{tail}{phase}")
        if step.get("error"):
            lines.append(f"          {step['error']}")
        for failure in step.get("failures", []):
            lines.append(f"          {failure['path']} {failure['op']} {failure['expected']!r}"
                         f" — actual: {failure['actual']!r}")
    if report["handoffs"]:
        lines.append("\nNeeds a human to look at:")
        lines.extend(f"  - {m}" for m in report["handoffs"])
    counts = report["counts"]
    lines.append(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, "
                 f"{counts[HANDOFF]} for review, {counts[SKIPPED]} skipped")
    return "\n".join(lines)
