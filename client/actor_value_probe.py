#!/usr/bin/env python3
"""Read a runtime actor value with AgentBridge's strict repeated-read guard."""

from __future__ import annotations

import argparse
import json

import bridge


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True,
                        help="runtime reference FormID, e.g. 0x14 or 0x000A2C94")
    parser.add_argument("--name", required=True, help="actor value, e.g. HeavyArmor")
    parser.add_argument("--consecutive", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=12)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()

    result = bridge.actor_value(
        args.name,
        args.ref,
        consecutive=args.consecutive,
        max_attempts=args.max_attempts,
        interval=args.interval,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
