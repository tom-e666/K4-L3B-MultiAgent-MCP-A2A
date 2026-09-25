"""One-off exploration: dump raw MCP evidence for every case into debug/ (never packaged).

Runs against the CURRENT active run (does not start a new run, does not touch
outputs/ or traces/). After analysis, do a fresh `day09 run` for the final submission.
Usage (venv active, repo root):  python scripts/explore_dump.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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


async def main() -> None:
    settings = Settings.load(ROOT)
    contracts = Contracts(ROOT / "contracts" / "schemas")
    case_set = load_case_set(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gw:
        listed = await gw._session.list_tools()
        schema_of = lambda t: (getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {})
        tools = {t.name: schema_of(t) for t in listed.tools}
        (ROOT / "debug" / "tools.json").write_text(json.dumps(
            [{"name": t.name, "description": t.description, "input": schema_of(t)}
             for t in listed.tools], indent=2, ensure_ascii=False), encoding="utf-8")
        print("tools:", sorted(tools))
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
                    return ev
                except Exception as exc:  # record failures too
                    records.append({"tool": tool, "args": args, "error": str(exc)})
                    return None

            values = {
                "customer_unique_id": [case.get("customer_unique_id_hint")],
                "policy_version": [case.get("policy_version")],
                "order_id": list(case.get("candidate_order_ids", [])),
            }
            # first pass: tools whose required params we can fill
            for name, schema in tools.items():
                req = [p for p in schema.get("required", []) if p != "case_id"]
                if not all(p in values for p in req):
                    continue
                if not req:
                    await call(name)
                    continue
                if req == ["order_id"]:
                    for oid in values["order_id"]:
                        await call(name, order_id=oid)
                else:
                    await call(name, **{p: values[p][0] for p in req})
            data = [r.get("evidence", {}).get("data") for r in records]
            values["seller_id"] = _ids(data, "seller_id")
            values["product_id"] = _ids(data, "product_id")
            values["payment_reference"] = _ids(data, "payment_reference")
            values["shipment_id"] = _ids(data, "shipment_id")
            for name, schema in tools.items():
                req = [p for p in schema.get("required", []) if p != "case_id"]
                if any(r["tool"] == name for r in records) or not req:
                    continue
                if all(values.get(p) for p in req):
                    await call(name, **{p: values[p][0] for p in req})
                else:
                    records.append({"tool": name, "skipped_required": req})
            target.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
            print(case_id, len(records), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
