"""Build diagnostic submission variants from the CURRENT outputs/ + trace (no MCP calls).

Each variant changes one thing so the public breakdown shows its effect.
Usage: python scripts/make_variant.py
"""
from __future__ import annotations

import copy
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYMENT_ONLY = {"valid_split_payment", "payment_mismatch", "duplicate_charge", "refund_pending",
                "refund_failed", "canceled_order_paid", "unavailable_order_paid"}


def load():
    trace_text = (ROOT / "traces" / "trace.jsonl").read_text(encoding="utf-8")
    tool_of = {}
    for line in trace_text.splitlines():
        e = json.loads(line)
        for ref in e.get("evidence_refs") or []:
            if e.get("tool_name"):
                tool_of[ref] = e["tool_name"]
    outputs = {p.name: json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((ROOT / "outputs").glob("*.json"))}
    topics = {}
    for name in outputs:
        case = json.loads((ROOT / "inputs" / name).read_text(encoding="utf-8"))
        topics[name] = next(c["topic"] for c in case["customer_request"]["claims"]
                            if c["topic"] != "requested_full_refund")
    return trace_text, tool_of, outputs, topics


def drop_tools(out, tool_of, tools):
    keep = lambda refs: [r for r in refs if tool_of.get(r) not in tools]  # noqa: E731
    out["evidence_refs"] = keep(out["evidence_refs"])
    for claim in out.get("claim_assessments", []):
        claim["evidence_refs"] = keep(claim["evidence_refs"])


def build(name, transform):
    trace_text, tool_of, outputs, topics = load()
    with zipfile.ZipFile(ROOT / "dist" / "submission.zip") as src:
        manifest = json.loads(src.read("manifest.json"))
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    target = ROOT / "dist" / f"variant_{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        z.writestr("trace.jsonl", trace_text)
        for fname, out in outputs.items():
            out = copy.deepcopy(out)
            transform(out, topics[fname], tool_of)
            z.writestr(f"outputs/{fname}", json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print("OK", target.name)


def v_noproduct(out, topic, tool_of):
    drop_tools(out, tool_of, {"get_product_context"})


def v_noship_payment(out, topic, tool_of):
    if topic in PAYMENT_ONLY:
        drop_tools(out, tool_of, {"get_shipment_summary"})


def v_unsupported_verdict(out, topic, tool_of):
    if topic == "unsupported_claim":
        for claim in out.get("claim_assessments", []):
            if claim["verdict"] == "supported":
                claim["verdict"] = "unsupported"


def v_full_refund_supported(out, topic, tool_of):
    fin = out["financial_resolution"]["recommended_refund_brl"]
    refundable = out["payment_analysis"]["refundable_total_brl"]
    for claim in out.get("claim_assessments", []):
        if (claim["verdict"] == "partially_supported" and refundable
                and abs(fin - refundable) < 0.005):
            claim["verdict"] = "supported"


def v_noorder(out, topic, tool_of):
    drop_tools(out, tool_of, {"get_order"})


if __name__ == "__main__":
    build("A_noproduct", v_noproduct)
    build("B_noship_payment", v_noship_payment)
    build("C_unsupported_verdict", v_unsupported_verdict)
    build("D_full_refund_supported", v_full_refund_supported)
    build("E_noorder", v_noorder)
