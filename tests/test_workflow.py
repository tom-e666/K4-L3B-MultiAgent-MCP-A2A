import asyncio
import hashlib
import json
from pathlib import Path

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import _date, _selected_order, solve_case

ROOT = Path(__file__).resolve().parents[1]
ORDER = "a" * 32


class FakeGateway:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def list_tools(self):
        return list(self.data)

    async def call(self, name, *, case_id, **arguments):
        self.calls.append((name, case_id, arguments))
        return {
            "evidence_ref": "ev_" + hashlib.sha256(name.encode()).hexdigest()[:24],
            "data": self.data[name],
        }


def fixture_case():
    return {
        "case_id": "L3B_CASE_TEST",
        "opened_at": "2018-01-10T09:00:00-03:00",
        "customer_unique_id_hint": "customer-test",
        "candidate_order_ids": [ORDER, "candidate-test"],
        "customer_request": {
            "claimed_order_id": ORDER,
            "claims": [
                {"claim_id": "claim-1", "topic": "late_delivery_logistics"},
                {"claim_id": "claim-2", "topic": "requested_full_refund"},
            ],
        },
        "investigation_scope": {"include_product_context": True},
        "policy_version": "EC_POLICY_V2",
    }


def test_temporal_resolution_uses_history_and_traces_real_refs(tmp_path):
    old = {
        "order_id": ORDER,
        "order_status": "delivered",
        "order_purchase_timestamp": "2017-12-20T09:00:00-03:00",
        "order_delivered_carrier_date": "2017-12-22T09:00:00-03:00",
        "order_delivered_customer_date": "2018-01-04T09:00:00-03:00",
        "order_estimated_delivery_date": "2017-12-30T09:00:00-03:00",
    }
    new = {**old, "order_purchase_timestamp": "2018-05-11T09:00:00-03:00"}
    gateway = FakeGateway(
        {
            "get_customer_history": {"customer_unique_id": "customer-test", "orders": [new, old]},
            "get_order": new,
            "get_order_items": [
                {
                    "order_item_id": "item-1",
                    "seller_id": "seller-1",
                    "shipping_limit_date": "2017-12-23T09:00:00-03:00",
                    "price": "79",
                    "freight_value": "16",
                }
            ],
            "get_shipment_summary": {
                "order_status": "delivered",
                "shipping_limits": [
                    {
                        "seller_id": "seller-1",
                        "shipping_limit_at": "2017-12-23T09:00:00-03:00",
                    }
                ],
            },
            "get_payment_timeline": {
                "events": [
                    {
                        "event_at": "2017-12-20T10:00:00-03:00",
                        "event_type": "captured",
                        "status": "confirmed",
                        "amount_brl": "16",
                    }
                ]
            },
            "get_product_context": [{"order_item_id": "item-1", "seller_id": "seller-1"}],
            "get_policy": {
                "rules": {
                    "late_delivery_logistics": {
                        "case_status": "action_required",
                        "refund_brl": 16,
                        "recommended_action": "refund_freight",
                        "responsible_parties": [
                            {"party_type": "logistics_provider", "party_id": None}
                        ],
                    }
                }
            },
        }
    )
    contracts = Contracts(ROOT / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    result = asyncio.run(solve_case(fixture_case(), gateway, trace))
    contracts.validate_output(result, "workflow test")
    assert result["assessment"]["primary_issue"] == "late_delivery_logistics"
    assert result["financial_resolution"]["recommended_refund_brl"] == 16
    assert result["data_conflicts"][0]["resolution_code"] == "TEMPORAL_CASE_MATCH"
    assert result["entity_resolution"]["rejected_candidates"] == ["candidate-test"]
    assert all(arguments.get("order_id") != "candidate-test" for _, _, arguments in gateway.calls)
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    consumed = {
        ref
        for event in events
        if event["event_type"] == "tool_result_consumed"
        for ref in event["evidence_refs"]
    }
    assert set(result["evidence_refs"]) == consumed
    assert any(event["event_type"] == "verification_completed" for event in events)


def test_missing_evidence_stays_unresolved(tmp_path):
    gateway = FakeGateway({})
    contracts = Contracts(ROOT / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    result = asyncio.run(solve_case(fixture_case(), gateway, trace))
    contracts.validate_output(result, "missing evidence")
    assert result["assessment"]["primary_issue"] == "insufficient_evidence"
    assert result["entity_resolution"]["status"] == "not_found"
    assert result["evidence_refs"] == []
    assert result["financial_resolution"]["recommended_refund_brl"] == 0


def test_claim_corrobated_history_row_beats_unrelated_recent_order():
    late = {
        "order_id": ORDER,
        "order_status": "delivered",
        "order_purchase_timestamp": "2018-08-05T09:00:00-03:00",
        "order_delivered_customer_date": "2018-08-20T09:00:00-03:00",
        "order_estimated_delivery_date": "2018-08-15T09:00:00-03:00",
    }
    recent = {
        **late,
        "order_purchase_timestamp": "2018-08-14T09:00:00-03:00",
        "order_delivered_customer_date": "2018-08-23T09:00:00-03:00",
        "order_estimated_delivery_date": "2018-08-24T09:00:00-03:00",
    }
    history = {"data": {"orders": [recent, late]}}
    selected, next_purchase = _selected_order(
        history,
        {"data": recent},
        [ORDER],
        _date("2018-08-17T09:00:00-03:00"),
        "late_delivery_logistics",
    )
    assert selected == late
    assert next_purchase == _date(recent["order_purchase_timestamp"])


def test_split_payment_reconciles_two_captures_when_colliding_row_is_present(tmp_path):
    case = fixture_case()
    case["customer_request"]["claims"][0]["topic"] = "valid_split_payment"
    order = {
        "order_id": ORDER,
        "order_status": "delivered",
        "order_purchase_timestamp": "2017-12-20T09:00:00-03:00",
        "order_delivered_carrier_date": "2017-12-22T09:00:00-03:00",
        "order_delivered_customer_date": "2017-12-29T09:00:00-03:00",
        "order_estimated_delivery_date": "2017-12-30T09:00:00-03:00",
    }
    item = {
        "order_item_id": "item-1",
        "seller_id": "seller-1",
        "shipping_limit_date": "2017-12-23T09:00:00-03:00",
        "price": "79",
        "freight_value": "10",
    }
    gateway = FakeGateway(
        {
            "get_customer_history": {
                "customer_unique_id": "customer-test",
                "orders": [order, order],
            },
            "get_order": order,
            "get_order_items": [item, item],
            "get_shipment_summary": {"order_status": "delivered", "shipping_limits": []},
            "get_payment_timeline": {
                "events": [
                    {
                        "event_at": "2017-12-20T10:00:00-03:00",
                        "event_type": "captured",
                        "status": "confirmed",
                        "amount_brl": amount,
                    }
                    for amount in ("52", "44.50", "44.50")
                ]
            },
            "get_product_context": [],
            "get_policy": {
                "rules": {
                    "valid_split_payment": {
                        "case_status": "no_action",
                        "refund_brl": 0,
                        "recommended_action": "document_no_action",
                    }
                }
            },
        }
    )
    contracts = Contracts(ROOT / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    result = asyncio.run(solve_case(case, gateway, trace))
    contracts.validate_output(result, "split payment")
    assert result["assessment"]["primary_issue"] == "valid_split_payment"
    assert result["payment_analysis"]["captured_total_brl"] == 89
    assert result["data_conflicts"][0]["resolution_code"] == "RECONCILED_SPLIT_SUBSET"
