"""CrashLogger 時間、uptime 與 crash triage。"""

from __future__ import annotations

import re
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from mo2ctl_env import (  # noqa: F401
    Fail,
)


def parse_crash_time(path: Path, local_tz: tzinfo | None = None) -> datetime | None:
    match = re.match(r"crash-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.log$", path.name)
    if not match:
        return None
    stamp = datetime(*map(int, match.groups()))
    localized = stamp.astimezone() if local_tz is None else stamp.replace(tzinfo=local_tz)
    return localized.astimezone(UTC)


def parse_uptime_ms(text: str) -> int | None:
    patterns = [
        r"uptime[: ]+([0-9,]+)\s*ms",
        r"uptime[: ]+([0-9,]+)\s*milliseconds",
        r"Uptime\s*:\s*([0-9,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def uptime_bin(ms: int | None) -> str:
    if ms is None:
        return "unknown"
    if ms <= 8000:
        return "load/plugin-conflict window"
    if ms <= 90000:
        return "entry/initialization window"
    return "content/playtime window"


def summarize_crash_log(path: Path, mod: str | None, local_tz: tzinfo | None = None) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    modules = []
    for match in re.finditer(r"([A-Za-z0-9_ .'-]+\.(?:dll|exe))\+([0-9A-Fa-f]+)", text, flags=re.IGNORECASE):
        module = f"{match.group(1)}+{match.group(2)}"
        if module not in modules:
            modules.append(module)
        if len(modules) == 3:
            break
    uptime = parse_uptime_ms(text)
    has_stack = "call stack" in text.lower()
    relevant = bool(mod and mod.lower() in text.lower())
    exception = ""
    for line in text.splitlines():
        lowered = line.lower()
        if "exception" in lowered or "access violation" in lowered:
            exception = line.strip()
            break
    crash_time = parse_crash_time(path, local_tz)
    return {
        "file": str(path),
        "time": crash_time.isoformat() if crash_time else None,
        "has_call_stack": has_stack,
        "top_modules": modules,
        "mentions_mod": relevant,
        "exception": exception,
        "uptime_ms": uptime,
        "uptime_bin": uptime_bin(uptime),
        "attribution": "candidate" if relevant or any(mod and mod.lower() in m.lower() for m in modules)
                       else "unable_to_attribute" if not has_stack else "not_matched_to_mod",
    }


def crash_log_dir_from_status(text: str) -> Path | None:
    match = re.search(r"crash_logs:\s+(.+?)\s+\(", text)
    if not match:
        return None
    return Path(match.group(1)).expanduser()


def crash_triage_from_capture(results: list[dict], args, local_tz: tzinfo | None = None) -> dict:
    if not args.crash_since:
        return {"checked": False, "reason": "no --crash-since provided"}
    status_text = next((r.get("text", "") for r in results if r.get("tool") == "housecarl_load_order_status"), "")
    folder = crash_log_dir_from_status(status_text)
    if not folder or not folder.is_dir():
        return {"checked": False, "reason": "crash log folder not configured or missing"}
    try:
        since = datetime.fromisoformat(args.crash_since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Fail(f"--crash-since must be ISO datetime, got {args.crash_since!r}") from exc
    if since.tzinfo is None:
        since = since.astimezone() if local_tz is None else since.replace(tzinfo=local_tz)
    since = since.astimezone(UTC)

    chosen = []
    last_time: datetime | None = None
    for path in sorted(folder.glob("crash-*.log")):
        when = parse_crash_time(path, local_tz)
        if not when or when < since:
            continue
        if last_time and abs((when - last_time).total_seconds()) <= 1:
            continue
        chosen.append(summarize_crash_log(path, args.mod, local_tz))
        last_time = when
    return {"checked": True, "folder": str(folder), "since": since.isoformat(), "new_logs": chosen}
