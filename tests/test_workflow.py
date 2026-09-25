from __future__ import annotations

import asyncio
import json
from pathlib import Path

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


class FakeGateway:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.calls: list[tuple[str, str]] = []

    async def call(self, name: str, *, case_id: str, **arguments: str) -> dict:
        self.calls.append((name, case_id))
        if name not in self.data:
            raise RuntimeError("not found")
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": "ev_" + name.replace("_", "") + "x" * 24,
            "result_hash": "sha256:" + "a" * 64,
            "domain": "policy" if name == "get_policy" else "order",
            "data": self.data[name],
        }


def test_resolves_timeline_and_limits_refund(tmp_path: Path) -> None:
    case = {
        "case_id": "L3B_CASE_001", "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_unique_id_hint": "customer-1",
        "candidate_order_ids": ["order-1", "candidate-1"],
        "policy_version": "EC_POLICY_V2",
        "investigation_scope": {"include_product_context": True},
        "customer_request": {"claims": [
            {"claim_id": "claim-a", "topic": "late_delivery_logistics"},
            {"claim_id": "claim-b", "topic": "requested_full_refund"},
        ]},
    }
    past = "2017-12-20T09:00:00-03:00"
    future = "2018-05-11T09:00:00-03:00"
    selected = {
        "order_id": "order-1", "order_status": "delivered",
        "order_purchase_timestamp": past,
        "order_delivered_carrier_date": "2017-12-22T09:00:00-03:00",
        "order_delivered_customer_date": "2018-01-04T09:00:00-03:00",
        "order_estimated_delivery_date": "2017-12-30T09:00:00-03:00",
    }
    data = {
        "get_customer_history": {"customer_unique_id": "customer-1", "orders": [
            {**selected, "order_purchase_timestamp": future}, selected,
        ]},
        "get_order": {**selected, "order_purchase_timestamp": future},
        "get_order_items": [{"order_item_id": "item-1", "seller_id": "seller-1",
                             "shipping_limit_date": "2017-12-23T09:00:00-03:00",
                             "price": "79.00", "freight_value": "18.00"}],
        "get_product_context": [],
        "get_shipment_summary": {"events": []},
        "get_payment_timeline": {"events": [
            {"event_at": "2017-12-20T10:00:00-03:00", "event_type": "captured",
             "amount_brl": "16.00", "status": "confirmed"},
            {"event_at": "2018-05-11T10:00:00-03:00", "event_type": "captured",
             "amount_brl": "89.00", "status": "confirmed"},
        ]},
        "get_policy": {"rules": {"late_delivery_logistics": {
            "case_status": "action_required", "recommended_action": "refund_freight",
            "refund_brl": 16, "responsible_parties": [
                {"party_type": "logistics_provider", "party_id": None},
            ],
        }}},
    }
    gateway = FakeGateway(data)
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    output = asyncio.run(solve_case(case, gateway, TraceWriter(trace_path, contracts)))
    contracts.validate_output(output, "test")
    assert output["entity_resolution"]["resolved_order_ids"] == ["order-1"]
    assert output["entity_resolution"]["rejected_candidates"] == ["candidate-1"]
    assert output["assessment"]["primary_issue"] == "late_delivery_logistics"
    assert output["financial_resolution"]["recommended_refund_brl"] == 16
    assert output["payment_analysis"]["captured_total_brl"] == 16
    assert output["shipment_analysis"]["verdict"] == "logistics_delay"
    assert output["data_conflicts"][0]["selected_source"] == "customer_history"
    events = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert any(event["event_type"] == "verification_completed" for event in events)
    assert all(seen_case == case["case_id"] for _, seen_case in gateway.calls)
    assert len(gateway.calls) == 7


def test_missing_entity_does_not_invent_answer(tmp_path: Path) -> None:
    case = {"case_id": "L3B_CASE_002", "opened_at": "2018-02-02T09:00:00-03:00",
            "candidate_order_ids": ["order-2", "candidate-2"],
            "customer_unique_id_hint": "customer-2", "customer_request": {"claims": []}}
    gateway = FakeGateway({"get_customer_history": {"orders": []}})
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    output = asyncio.run(solve_case(case, gateway, trace))
    contracts.validate_output(output, "test")
    assert output["assessment"]["primary_issue"] == "insufficient_evidence"
    assert output["entity_resolution"]["resolved_order_ids"] == []
    assert len(gateway.calls) == 1
