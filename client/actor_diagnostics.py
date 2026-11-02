"""Version 1 actor diagnostics parser; DLL acquisition is not implemented yet."""

from __future__ import annotations

import argparse
import copy
import json
import re
import urllib.parse
from pathlib import Path

SCHEMA_VERSION = 1
CAPABILITY = "actor_diagnostics"


def failure(status: str, error: str) -> dict:
    return {"ok": False, "status": status, "error": error}


def runtime_form_id(value: str | int) -> int:
    """Integer IDs are numeric; strings are hexadecimal, as in existing routes."""
    if isinstance(value, str) and re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,8}", value):
        value = int(value, 16)
    if type(value) is not int or not 0 < value <= 0xFFFFFFFF:
        raise ValueError("form_id must be a non-zero uint32 or hexadecimal string")
    return value


def _object(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _form(value, path: str, *, style: bool = False) -> None:
    value = _object(value, path)
    if type(value.get("form_id")) is not int:
        raise ValueError(f"{path}.form_id must be an integer")
    runtime_form_id(value["form_id"])
    for key in ("name", "editor_id"):
        if not isinstance(value.get(key), str):
            raise ValueError(f"{path}.{key} must be a string (empty is valid)")
    tag = value.get("form_type")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Z0-9_]{4}", tag):
        raise ValueError(f"{path}.form_type must be a four-character record signature")
    if style and tag != "CSTY":
        raise ValueError(f"{path}.form_type must be CSTY")


def _reading(value, path: str, *, style: bool = False) -> None:
    value = _object(value, path)
    status = value.get("status")
    if status == "value":
        if "reason" in value:
            raise ValueError(f"{path}: value cannot have an unavailable reason")
        _form(value.get("value"), f"{path}.value", style=style)
    elif status == "empty":
        if "value" not in value or value["value"] is not None or "reason" in value:
            raise ValueError(f"{path}: empty requires explicit null value and no reason")
    elif status == "unavailable":
        if "value" in value or not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise ValueError(f"{path}: unavailable requires a reason and no value")
    else:
        raise ValueError(f"{path}.status must be value, empty or unavailable")


def parse_actor_diagnostics(payload, *, form_id: str | int) -> dict:
    """Validate a response without inferring missing hands, spells or CSTY values.

    ok=True means a valid snapshot, even when individual readings are unavailable.
    Unknown additive metadata is preserved; missing v1 readings fail closed.
    """
    try:
        expected = runtime_form_id(form_id)
    except ValueError as exc:
        return failure("invalid_request", str(exc))
    try:
        payload = _object(payload, "response")
        if payload.get("ok") is False:
            if not isinstance(payload.get("error"), str) or not payload["error"].strip():
                raise ValueError("failed response requires a non-empty error")
            status = payload.get("status", "query_failed")
            if status not in ("unsupported", "query_failed"):
                raise ValueError("failed response has an invalid status")
            return failure(status, payload["error"])
        if payload.get("ok") is not True:
            raise ValueError("response.ok must be a boolean")
        version = payload.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ValueError("response.schema_version must be 1")
        actor = _object(payload.get("actor"), "actor")
        for key in ("form_id", "base_form_id"):
            if type(actor.get(key)) is not int:
                raise ValueError(f"actor.{key} must be an integer")
            runtime_form_id(actor[key])
        if actor["form_id"] != expected:
            raise ValueError("actor.form_id does not match the requested runtime reference")
        if not isinstance(actor.get("name"), str):
            raise ValueError("actor.name must be a string")
        for group in ("equipped", "selected_spells"):
            readings = _object(payload.get(group), group)
            for hand in ("right", "left"):
                _reading(readings.get(hand), f"{group}.{hand}")
        styles = _object(payload.get("combat_style"), "combat_style")
        for layer in ("base", "reference_override"):
            _reading(styles.get(layer), f"combat_style.{layer}", style=True)
        return copy.deepcopy(payload)
    except ValueError as exc:
        return failure("invalid_response", str(exc))


def query_actor_diagnostics(request, *, form_id: str | int, scope: str, timeout: float) -> dict:
    """Negotiate before issuing the proposed GET; never use console fallback."""
    try:
        reference = runtime_form_id(form_id)
        if reference == 0x14:
            raise ValueError("diagnostics requires an NPC reference, not the player")
        if scope not in ("cell", "loaded"):
            raise ValueError("scope must be 'cell' or 'loaded'")
    except ValueError as exc:
        return failure("invalid_request", str(exc))

    try:
        hello = request("GET", "/ping", timeout=timeout)
        hello = _object(hello, "ping")
        if hello.get("ok") is False:
            return parse_actor_diagnostics(hello, form_id=reference)
        if hello.get("ok") is not True or hello.get("plugin") != "AgentBridge":
            raise ValueError("ping must identify AgentBridge with ok=true")
        if "capabilities" not in hello:
            return failure("unsupported", "DLL does not advertise actor_diagnostics")
        caps = _object(hello["capabilities"], "ping.capabilities")
        if CAPABILITY not in caps:
            return failure("unsupported", "DLL does not advertise actor_diagnostics")
        versions = caps[CAPABILITY]
        if not isinstance(versions, list) or any(type(v) is not int or v < 1 for v in versions):
            raise ValueError("actor_diagnostics capability must list positive integer versions")
        if SCHEMA_VERSION not in versions:
            return failure("unsupported", "DLL does not support actor_diagnostics schema 1")
        query = urllib.parse.urlencode({"form_id": f"0x{reference:08X}", "scope": scope,
                                        "schema_version": SCHEMA_VERSION})
        payload = request("GET", f"/actor/diagnostics?{query}", timeout=timeout)
        return parse_actor_diagnostics(payload, form_id=reference)
    except (OSError, TimeoutError) as exc:
        return failure("query_failed", str(exc) or "transport failed")
    except ValueError as exc:
        return failure("invalid_response", str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="runtime NPC reference FormID")
    parser.add_argument("--snapshot", type=Path, help="parse a saved response offline")
    parser.add_argument("--scope", choices=("cell", "loaded"), default="cell")
    args = parser.parse_args()
    if args.snapshot:
        try:
            payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
            result = parse_actor_diagnostics(payload, form_id=args.ref)
        except (OSError, ValueError) as exc:
            result = failure("invalid_response", str(exc))
    else:
        import bridge
        result = bridge.actor_diagnostics(form_id=args.ref, scope=args.scope)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
