"""One-off exploration: dump raw MCP evidence for every case into debug/ (never packaged).

Uses the CURRENT active run; does not touch outputs/ or traces/. Resumable.
Usage (venv active, repo root):  python scripts/explore_dump.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Handle Python 3.10 compatibility for BaseExceptionGroup
try:
    BaseExceptionGroup
except NameError:
    BaseExceptionGroup = Exception

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from student_agent.cases import load_case_set  # noqa: E402
from student_agent.config import Settings  # noqa: E402
from student_agent.contracts import Contracts  # noqa: E402
from student_agent.mcp_gateway import connect_gateway  # noqa: E402

OUT = ROOT / "debug" / "evidence"


def _ids(value, key):
    found = []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == key and isinstance(x, str):
                    found.append(x)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return list(dict.fromkeys(found))


def _schema(tool):
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}


async def main() -> None:
    settings = Settings.load(ROOT)
    contracts = Contracts(ROOT / "contracts" / "schemas")
    case_set = load_case_set(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gw:
        listed = await gw._session.list_tools()
        tools = {t.name: _schema(t) for t in listed.tools}
        for case_id in case_set.case_ids:
            if only and case_id not in only:
                continue
            target = OUT / f"{case_id}.json"
            if target.exists():
                continue
            case = case_set.cases[case_id]
            records = []

            async def call(tool, **args):
                try:
                    ev = await gw.call(tool, case_id=case_id, **args)
                    records.append({"tool": tool, "args": args, "evidence": ev})
                except RuntimeError as exc:  # tool-level error: record it
                    records.append({"tool": tool, "args": args, "error": str(exc)})

            values = {
                "customer_unique_id": [case.get("customer_unique_id_hint")],
                "policy_version": [case.get("policy_version")],
                "order_id": list(case.get("candidate_order_ids", [])),
            }
            for name, schema in tools.items():
                req = [p for p in schema.get("required", []) if p != "case_id"]
                if not all(p in values for p in req):
                    continue
                if req == ["order_id"]:
                    for oid in values["order_id"]:
                        await call(name, order_id=oid)
                else:
                    await call(name, **{p: values[p][0] for p in req})
            target.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
            print(case_id, len(records), flush=True)


if __name__ == "__main__":
    for attempt in range(1, 11):
        try:
            asyncio.run(main())
            print("DONE")
            break
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # network drop: reconnect and resume
            leaf = exc
            while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
                leaf = leaf.exceptions[0]
            print(f"connection error: {type(leaf).__name__}: {leaf!r}"[:500], flush=True)
            print(f"retry {attempt}/10", flush=True)
            time.sleep(5 * attempt)
