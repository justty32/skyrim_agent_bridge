"""Static-gate 結果分類與 baseline 比較。"""

from __future__ import annotations

import re

from mo2ctl_env import (  # noqa: F401
    ENGINE_LOADORDER_CHURN, Fail, STATIC_GATE_FORMAT,
)


def tool_key(result: dict) -> str:
    args = result.get("arguments") or {}
    suffix = ""
    if "formid" in args:
        suffix = f":{args['formid']}"
    if "mesh_path" in args:
        suffix = f":{args['mesh_path']}"
    if "asset_paths" in args:
        suffix = ":" + ",".join(args["asset_paths"])
    return f"{result.get('tool')}{suffix}"


def normalize_load_order_warnings(text: str) -> list[str]:
    warnings = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean.startswith("- "):
            continue
        lowered = clean.lower()
        if any(cc in lowered for cc in ENGINE_LOADORDER_CHURN):
            continue
        if "warning" in lowered or "load order lists" in lowered or "missing" in lowered:
            warnings.append(clean)
    return warnings


def skse_diagnostics(text: str) -> tuple[list[str], list[str]]:
    diagnostics = []
    incomplete = []
    section = None
    expected = None
    seen = 0

    def finish_section() -> None:
        nonlocal section, expected, seen
        if section is not None and expected is not None and seen != expected:
            incomplete.append(f"{section}: rendered {seen} of {expected} diagnostic item(s)")
        section = None
        expected = None
        seen = 0

    for line in text.splitlines():
        clean = line.strip()
        lowered = line.lower()
        locked_header = re.match(
            r"^\[!\]\s+version-locked plugins\s+\((\d+)\)", clean,
            flags=re.IGNORECASE,
        )
        contested_header = re.match(
            r"^contested dlls\s+.*\((\d+)\):$", clean,
            flags=re.IGNORECASE,
        )
        if locked_header:
            finish_section()
            section = "version-locked"
            expected = int(locked_header.group(1))
            continue
        if contested_header:
            finish_section()
            section = "contested"
            expected = int(contested_header.group(1))
            continue
        if not clean:
            finish_section()
            continue
        if section and clean.startswith("- "):
            diagnostics.append(f"{section}: {clean[2:]}")
            seen += 1
            continue
        if section and clean.startswith("... [showing "):
            incomplete.append(f"{section}: {clean}")
            continue
        # Retain compatibility with older/compact houseCARL output while avoiding
        # aggregate lines such as `compat: 73 Address Library ... 5 version-LOCKED`.
        if (lowered.strip().startswith("contested dll:") or
                lowered.strip().startswith("version-locked dll:") or
                " locked to " in f" {lowered.strip()} "):
            diagnostics.append(clean)
    finish_section()
    return sorted(set(diagnostics)), sorted(set(incomplete))


def regex_count(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def validate_scripts_has_findings(text: str) -> bool:
    lowered = text.lower()
    counts = [
        regex_count(r"(\d+)\s+unbound", text),
        regex_count(r"(\d+)\s+bound-but-null", text),
        regex_count(r"(\d+)\s+unverifiable", text),
    ]
    if any(count is not None for count in counts):
        return any((count or 0) > 0 for count in counts)
    return any(marker in lowered for marker in ("[unbound]", "bound-but-null", "unverifiable"))


def classify_static_result(current: dict, baseline: dict | None = None) -> dict:
    tool = current.get("tool", "")
    text = current.get("text", "")
    base_text = (baseline or {}).get("text", "")
    status = "pass"
    findings: list[str] = []

    if current.get("is_error"):
        return {"status": "fail", "findings": ["houseCARL tool returned isError"]}

    if tool == "housecarl_load_order_status":
        warnings = normalize_load_order_warnings(text)
        base_warnings = normalize_load_order_warnings(base_text) if baseline else []
        new = [w for w in warnings if w not in base_warnings]
        if new:
            status = "fail"
            findings.extend(new)
    elif tool == "housecarl_check_errors":
        missing = regex_count(r"(\d+)\s+missing master", text)
        base_missing = regex_count(r"(\d+)\s+missing master", base_text) if baseline else 0
        if missing is not None and missing > (base_missing or 0):
            status = "fail"
            findings.append(f"missing masters increased: {base_missing or 0} -> {missing}")
        elif baseline and text != base_text:
            status = "warn"
            findings.append("check_errors output differs from baseline; review raw report")
        elif not baseline and missing:
            status = "fail"
            findings.append(f"missing masters: {missing}")
    elif tool == "housecarl_skse_inventory":
        diagnostics, incomplete = skse_diagnostics(text)
        base_diagnostics, _base_incomplete = skse_diagnostics(base_text) if baseline else ([], [])
        if incomplete:
            status = "fail"
            findings.extend(f"SKSE diagnostic inventory incomplete: {item}" for item in incomplete)
        elif not baseline:
            if diagnostics:
                status = "warn"
                findings.append("SKSE diagnostics present; use a baseline to distinguish existing contested/locked DLLs")
        else:
            new = [d for d in diagnostics if d not in base_diagnostics]
            if new:
                status = "fail"
                findings.extend(new)
    elif tool == "housecarl_validate_scripts":
        has_findings = validate_scripts_has_findings(text)
        base_has_findings = validate_scripts_has_findings(base_text) if baseline else False
        if has_findings and not (baseline and text == base_text):
            scoped_plugins = bool((current.get("arguments") or {}).get("plugins"))
            status = "fail" if scoped_plugins or baseline else "warn"
            if baseline and base_has_findings and text != base_text:
                findings.append("script validation output differs from baseline; review raw report")
            else:
                findings.append("static validator reported script binding findings")
    else:
        lowered = text.lower()
        bad_markers = ["[error]", "missing master", "dangling", "unbound", "unverifiable", "absent", "could not", "failed"]
        if any(marker in lowered for marker in bad_markers):
            if baseline and text == base_text:
                status = "pass"
            else:
                scoped_plugins = bool((current.get("arguments") or {}).get("plugins"))
                status = "fail" if scoped_plugins or baseline else "warn"
                findings.append("static validator reported errors or unresolved assets")

    return {"status": status, "findings": findings}


def evaluate_static_gates(capture: dict, baseline: dict | None = None) -> dict:
    if capture.get("format") != STATIC_GATE_FORMAT:
        raise Fail("not a mo2ctl static-gates capture")
    baseline_by_key = {tool_key(item): item for item in (baseline or {}).get("tools", [])}
    gate_results = []
    status_rank = {"pass": 0, "warn": 1, "fail": 2}
    overall = "pass"
    for result in capture.get("tools", []):
        key = tool_key(result)
        classified = classify_static_result(result, baseline_by_key.get(key))
        gate_results.append({"key": key, **classified})
        if status_rank[classified["status"]] > status_rank[overall]:
            overall = classified["status"]

    crash = capture.get("crash_logs") or {}
    if crash.get("new_logs"):
        overall = "fail"
        gate_results.append({
            "key": "crash_logs",
            "status": "fail",
            "findings": [f"{len(crash['new_logs'])} crash log(s) matched triage window"],
        })

    return {
        "status": overall,
        "gates": gate_results,
    }
