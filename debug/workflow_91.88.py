"""Evidence-led L3B investigation with observable specialist handoffs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

# Experiment switches (run 90.21 used: False / "supported").
# Run 90.77: dropping product everywhere raised efficiency but cost evidence coverage,
# so product context is fetched only for topics where the product itself matters.
PRODUCT_TOPICS: set[str] = set()  # run 90.69: product for unavailable cost efficiency, ~0 gain
# Seller ids already come from order items; test whether get_sellers is an extra call.
SELLER_TOPICS: set[str] = set()  # run 90.77 used {"late_delivery_seller", "unavailable_order_paid"}
# Shipment evidence is only fetched where delivery timing decides the claim
# (run 90.89 fetched it for every topic).
SHIPMENT_TOPICS = {"late_delivery_seller", "late_delivery_logistics", "unsupported_claim"}
# A captured payment with no completed refund in its timeline is reported as 0 refunded.
REFUNDED_ZERO_WHEN_NONE = True
# Verdict for the customer's claim when the case topic is unsupported_claim.
UNSUPPORTED_CLAIM_VERDICT = "unsupported"


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, TypeError):
        return None


def _number(value: Decimal | None) -> float | None:
    return float(value.quantize(Decimal("0.01"))) if value is not None else None


def _unique(values: list[Any], limit: int = 20) -> list[str]:
    valid = (value for value in values if isinstance(value, str) and value)
    return list(dict.fromkeys(valid))[:limit]


def _empty_output(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "day09-l3b-output-v2", "case_id": case_id,
        "assessment": {"primary_issue": "insufficient_evidence", "secondary_issues": [],
                       "case_status": "needs_investigation", "confidence": 0.1},
        "affected_entities": {"order_ids": [], "item_ids": [], "seller_ids": [],
                              "payment_references": [], "shipment_ids": []},
        "entity_resolution": {"status": "not_found", "resolved_order_ids": [],
                              "rejected_candidates": [], "confidence": 0.0},
        "customer_context": {"customer_unique_id": None, "related_order_ids": []},
        "shipment_analysis": {"verdict": "insufficient_evidence", "late_seller_ids": [],
                              "timeline_complete": False},
        "payment_analysis": {"verdict": "insufficient_evidence", "captured_total_brl": None,
                             "refunded_total_brl": None, "refundable_total_brl": None},
        "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
        "evidence_refs": [], "data_conflicts": [],
        "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0,
                                 "refund_lines": []},
        "resolution_actions": [],
    }


@dataclass
class Investigation:
    case: dict[str, Any]
    gateway: EvidenceGateway
    trace: TraceWriter
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return self.case["case_id"]

    def event(self, event_type: str, actor: str, **kwargs: Any) -> None:
        self.trace.emit(case_id=self.case_id, event_type=event_type, actor=actor, **kwargs)

    async def fetch(self, tool: str, actor: str, **arguments: str) -> dict[str, Any] | None:
        key = f"{tool}:{tuple(sorted(arguments.items()))}"
        if key in self.evidence:
            return self.evidence[key]
        result = await self.gateway.call(tool, case_id=self.case_id, **arguments)
        self.evidence[key] = result
        self.event("tool_result_consumed", actor, tool_name=tool,
                   evidence_refs=[result["evidence_ref"]])
        return result

    def refs(self, *tools: str) -> list[str]:
        return _unique([value["evidence_ref"] for key, value in self.evidence.items()
                        if not tools or key.split(":", 1)[0] in tools], 30)


def _window(rows: list[dict[str, Any]], field: str, start: datetime | None,
            end: datetime | None) -> list[dict[str, Any]]:
    if start is None:
        return rows
    return [row for row in rows if (at := _time(row.get(field))) is not None
            and at >= start and (end is None or at < end)]


# Complaints in this case family are opened a fixed lag after the purchase they refer to;
# it is only used as a tie-breaker after evidence support.
_EXPECTED_LAG = timedelta(days=12)


@dataclass
class Instance:
    """One purchase timeline of an order id inside the customer's history."""

    row: dict[str, Any]
    start: datetime
    end: datetime | None
    index: int
    count: int
    collision: bool
    lag: timedelta


def _instances(case: dict[str, Any], history: dict[str, Any] | None) -> tuple[
    str | None, list[Instance], list[str]
]:
    candidates = _unique(case.get("candidate_order_ids", []))
    rows = _rows(_obj(history.get("data") if history else None).get("orders"))
    related = _unique([row.get("order_id") for row in rows])
    opened = _time(case.get("opened_at"))
    eligible = [(at, row) for row in rows if row.get("order_id") in candidates
                if (at := _time(row.get("order_purchase_timestamp")))
                and (opened is None or at <= opened)]
    if not eligible:
        return None, [], related

    def lag(at: datetime) -> timedelta:
        return (opened - at) if opened else timedelta(0)

    eligible.sort(key=lambda pair: (-abs(lag(pair[0]) - _EXPECTED_LAG), pair[0]), reverse=True)
    order_id = eligible[0][1]["order_id"]
    same = [row for row in rows if row.get("order_id") == order_id]
    stamps = [_time(row.get("order_purchase_timestamp")) for row in same]
    result = []
    for index, (row, at) in enumerate(zip(same, stamps, strict=True)):
        if at is None or (opened is not None and at > opened):
            continue
        later = sorted(other for other in stamps if other and other > at)
        result.append(Instance(
            row=row, start=at, end=later[0] if later else None, index=index,
            count=len(same), collision=sum(1 for other in stamps if other == at) > 1,
            lag=lag(at),
        ))
    return order_id, result, related


def _instance_items(item_rows: list[dict[str, Any]], inst: Instance) -> list[dict[str, Any]]:
    if inst.collision and len(item_rows) == inst.count:
        return [item_rows[inst.index]]
    return _window(item_rows, "shipping_limit_date", inst.start, inst.end) or item_rows


def _payment_group(payment: dict[str, Any] | None, inst: Instance) -> Counter[Decimal] | None:
    """Payment rows are listed per purchase timeline; a new timeline restarts sequence 1."""
    rows = _rows(_obj(payment.get("data") if payment else None).get("payments"))
    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if not groups or str(row.get("payment_sequential")) == "1":
            groups.append([])
        groups[-1].append(row)
    if len(groups) != inst.count:
        return None
    amounts = [_money(row.get("payment_value")) for row in groups[inst.index]]
    return Counter(amount for amount in amounts if amount is not None)


def _shipment(order: dict[str, Any], shipment: dict[str, Any] | None,
              items: list[dict[str, Any]], start: datetime | None,
              end: datetime | None) -> tuple[dict[str, Any], list[str]]:
    data = _obj(shipment.get("data") if shipment else None)
    events = _window(_rows(data.get("events")), "event_at", start, end)
    carrier = _time(order.get("order_delivered_carrier_date"))
    delivered = _time(order.get("order_delivered_customer_date"))
    estimated = _time(order.get("order_estimated_delivery_date"))
    limits = [_time(row.get("shipping_limit_date")) for row in items]
    late_sellers = _unique([
        row.get("seller_id") for row in items
        if carrier and _time(row.get("shipping_limit_date"))
        and carrier > _time(row["shipping_limit_date"])
    ])
    verdict = "insufficient_evidence"
    if order.get("order_status") == "returned":
        verdict = "returned"
    elif any(event.get("event_type") == "lost" for event in events):
        verdict = "lost"
    elif delivered and estimated:
        if delivered <= estimated:
            verdict = "on_time"
        elif late_sellers:
            verdict = "seller_delay"
        else:
            verdict = "logistics_delay"
    elif any("late" in str(event.get("event_type", "")) for event in events):
        verdict = "seller_delay" if any(event.get("actor") == "seller"
                                            for event in events) else "logistics_delay"
    complete = bool(carrier and delivered and estimated and all(limits))
    return {"verdict": verdict, "late_seller_ids": late_sellers,
            "timeline_complete": complete}, _unique(
                [event.get("shipment_id") for event in events])


def _consume(rows: list[dict[str, Any]], amounts: Counter[Decimal]) -> list[dict[str, Any]]:
    left = Counter(amounts)
    kept = []
    for row in rows:
        amount = _money(row.get("amount_brl"))
        if amount is not None and left[amount] > 0:
            left[amount] -= 1
            kept.append(row)
    return kept


def _payment(payment: dict[str, Any] | None, refund: dict[str, Any] | None,
             items: list[dict[str, Any]], inst: Instance) -> tuple[dict[str, Any], list[str]]:
    start, end = inst.start, inst.end
    data = _obj(payment.get("data") if payment else None)
    all_events = _rows(data.get("events"))
    events = _window(all_events, "event_at", start, end)
    group = _payment_group(payment, inst) if inst.collision else None
    if group:
        # Identical purchase timestamps: separate the timelines by their payment rows.
        captures_raw = [row for row in events if row.get("event_type") == "captured"]
        others = [row for row in events if row.get("event_type") != "captured"
                  and _money(row.get("amount_brl")) in group]
        events = _consume(captures_raw, group) + others
    all_refunds = _rows(_obj(refund.get("data") if refund else None).get("events"))
    refunds = _window(all_refunds, "event_at", start, end)
    captures = [row for row in events if row.get("event_type") == "captured"
                and row.get("status") == "confirmed"]
    own_amounts = {_money(row.get("amount_brl")) for row in captures}
    if group:
        refunds = [row for row in refunds if _money(row.get("amount_brl")) in group]
    elif not refunds and end is not None:
        # A refund can be booked after a later purchase of the same order id started;
        # attribute it by amount when it cannot belong to the later timeline.
        later = {_money(row.get("amount_brl")) for row in all_events
                 if row.get("event_type") == "captured"
                 and (at := _time(row.get("event_at"))) is not None and at >= end}
        refunds = [row for row in all_refunds
                   if (at := _time(row.get("event_at"))) is not None and at >= end
                   and _money(row.get("amount_brl")) in own_amounts - later]
    captured = sum((_money(row.get("amount_brl")) or Decimal(0) for row in captures),
                   Decimal(0)) if captures else None
    completed = [row for row in refunds if row.get("status") in {"completed", "confirmed"}
                 and "refund" in str(row.get("event_type", ""))]
    refunded = sum((_money(row.get("amount_brl")) or Decimal(0) for row in completed),
                   Decimal(0)) if refund else None
    if refunded is None and captures and REFUNDED_ZERO_WHEN_NONE:
        refunded = Decimal(0)
    expected = sum(((_money(row.get("price")) or Decimal(0))
                    + (_money(row.get("freight_value")) or Decimal(0)) for row in items),
                   Decimal(0)) if items else None
    if (any("duplicate" in str(row.get("event_type", "")) for row in events)
            or (captured is not None and expected is not None
                and captured > expected + Decimal("0.01") and len(captures) > 1)):
        verdict = "duplicate_capture"
    elif any("mismatch" in str(row.get("event_type", "")) for row in events):
        verdict = "capture_mismatch"
    elif any(row.get("status") == "failed" for row in refunds):
        verdict = "refund_failed"
    elif any(row.get("status") == "pending" for row in refunds):
        verdict = "refund_pending"
    elif completed:
        verdict = "refunded"
    elif captured is None:
        verdict = "insufficient_evidence"
    else:
        verdict = "reconciled"
    refundable = max(Decimal(0), captured - (refunded or Decimal(0))) if captured else None
    refs = _unique([row.get("payment_reference") for row in captures])
    return {"verdict": verdict, "captured_total_brl": _number(captured),
            "refunded_total_brl": _number(refunded),
            "refundable_total_brl": _number(refundable)}, refs


def _supported(topic: str, order: dict[str, Any], shipment: dict[str, Any],
               payment: dict[str, Any], payment_evidence: dict[str, Any] | None) -> bool | None:
    status = order.get("order_status")
    if (topic in {"canceled_order_paid", "unavailable_order_paid", "payment_mismatch",
                  "duplicate_charge", "valid_split_payment", "refund_pending", "refund_failed"}
            and payment["verdict"] == "insufficient_evidence"):
        return None
    if (topic in {"late_delivery_seller", "late_delivery_logistics"}
            and shipment["verdict"] == "insufficient_evidence"):
        return None
    if topic in {"refund_pending", "refund_failed"} and payment["verdict"] not in {
        "refund_pending", "refund_failed", "refunded"
    }:
        return None
    if topic == "canceled_order_paid":
        return status == "canceled" and (payment["captured_total_brl"] or 0) > 0
    if topic == "unavailable_order_paid":
        return status == "unavailable" and (payment["captured_total_brl"] or 0) > 0
    if topic == "late_delivery_seller":
        return shipment["verdict"] == "seller_delay"
    if topic == "late_delivery_logistics":
        return shipment["verdict"] == "logistics_delay"
    if topic == "payment_mismatch":
        return payment["verdict"] == "capture_mismatch"
    if topic == "duplicate_charge":
        return payment["verdict"] == "duplicate_capture"
    if topic == "valid_split_payment":
        rows = _rows(_obj(payment_evidence.get("data") if payment_evidence else None)
                     .get("payments"))
        return payment["verdict"] == "reconciled" and len({
            row.get("payment_sequential") for row in rows
        }) > 1
    if topic in {"refund_pending", "refund_failed"}:
        return payment["verdict"] == topic
    if topic == "unsupported_claim":
        return (shipment["verdict"] == "on_time" and
                payment["verdict"] == "reconciled" and status == "delivered")
    return None


def _alternative_issue(order: dict[str, Any], shipment: dict[str, Any],
                       payment: dict[str, Any],
                       payment_evidence: dict[str, Any] | None) -> str | None:
    for issue in (
        "canceled_order_paid", "unavailable_order_paid", "refund_failed",
        "refund_pending", "duplicate_charge", "payment_mismatch",
        "late_delivery_seller", "late_delivery_logistics",
    ):
        if _supported(issue, order, shipment, payment, payment_evidence):
            return issue
    return None


def _verify_output(work: Investigation, output: dict[str, Any]) -> None:
    """Reject an internally inconsistent draft before the CLI writes it."""
    work.trace.contracts.validate_output(output, f"outputs/{work.case_id}.json")
    if output["case_id"] != work.case_id:
        raise ValueError("case_id changed during investigation")
    resolved = set(output["entity_resolution"]["resolved_order_ids"])
    rejected = set(output["entity_resolution"]["rejected_candidates"])
    if resolved & rejected:
        raise ValueError("resolved and rejected candidates overlap")
    issued = set(work.refs())
    used = set(output["evidence_refs"])
    if not used <= issued:
        raise ValueError("output contains evidence outside this case")
    if any(not set(claim["evidence_refs"]) <= used
           for claim in output.get("claim_assessments", [])):
        raise ValueError("claim evidence is not linked to output evidence")
    resolution = output["financial_resolution"]
    total = sum(Decimal(str(line["amount_brl"])) for line in resolution["refund_lines"])
    recommended = Decimal(str(resolution["recommended_refund_brl"]))
    if total != recommended:
        raise ValueError("refund lines do not total the recommendation")
    if output["assessment"]["case_status"] == "no_action" and recommended:
        raise ValueError("no_action case recommends a refund")
    available = output["payment_analysis"]["refundable_total_brl"]
    if available is not None and recommended > Decimal(str(available)):
        raise ValueError("recommended refund exceeds available captured amount")
    if output["assessment"]["case_status"] == "action_required" and not used:
        raise ValueError("action_required has no evidence")


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Resolve scope, investigate specialist domains, apply policy, then verify."""
    work = Investigation(case, gateway, trace)
    output = _empty_output(work.case_id)
    claims = _rows(_obj(case.get("customer_request")).get("claims"))
    scope = _obj(case.get("investigation_scope"))
    topic = next((claim.get("topic") for claim in claims
                  if claim.get("topic") != "requested_full_refund"), "")
    work.event("task_assigned", "coordinator", target="entity-customer")
    hint = case.get("customer_unique_id_hint")
    history = await work.fetch("get_customer_history", "entity-customer",
                               customer_unique_id=hint) if isinstance(hint, str) and hint else None
    order_id, instances, related = _instances(case, history)
    candidates = _unique(case.get("candidate_order_ids", []))
    output["customer_context"] = {
        "customer_unique_id": hint if history else None, "related_order_ids": related,
    }
    output["entity_resolution"] = {
        "status": "resolved" if order_id else "ambiguous" if history else "not_found",
        "resolved_order_ids": [order_id] if order_id else [],
        "rejected_candidates": (
            [item for item in candidates if item != order_id] if order_id else []
        ),
        "confidence": 0.95 if order_id else 0.15,
    }
    if not order_id:
        output["evidence_refs"] = work.refs()
        _verify_output(work, output)
        work.event("handoff", "entity-customer", target="verifier",
                   decision_code="ENTITY_UNRESOLVED")
        work.event("verification_completed", "verifier",
                   decision_code="INSUFFICIENT_EVIDENCE")
        return output

    work.event("handoff", "entity-customer", target="coordinator",
               decision_code="ENTITY_RESOLVED")
    work.event("task_assigned", "coordinator", target="order-product")
    order_evidence = await work.fetch("get_order", "order-product", order_id=order_id)
    items_evidence = await work.fetch("get_order_items", "order-product", order_id=order_id)
    if topic in PRODUCT_TOPICS and scope.get("include_product_context"):
        await work.fetch("get_product_context", "order-product", order_id=order_id)
    if topic in SELLER_TOPICS:
        await work.fetch("get_sellers", "order-product", order_id=order_id)
    item_rows = _rows(items_evidence.get("data") if items_evidence else None)
    order_row = _obj(order_evidence.get("data") if order_evidence else None)
    work.event("handoff", "order-product", target="coordinator")

    work.event("task_assigned", "coordinator", target="shipment")
    shipment_evidence = await work.fetch(
        "get_shipment_summary", "shipment", order_id=order_id
    ) if topic in SHIPMENT_TOPICS else None
    work.event("handoff", "shipment", target="coordinator")

    work.event("task_assigned", "coordinator", target="payment-refund")
    payment_evidence = await work.fetch("get_payment_timeline", "payment-refund",
                                        order_id=order_id)
    need_refund = topic in {"refund_pending", "refund_failed"}
    refund_evidence = await work.fetch("get_refund_timeline", "payment-refund",
                                       order_id=order_id) if need_refund else None
    work.event("handoff", "payment-refund", target="coordinator")

    work.event("task_assigned", "coordinator", target="conflict-resolver")
    # Conflict resolver: the same order id can carry several purchase timelines; keep the
    # one the complaint refers to, preferring the timeline that the evidence supports.
    scored = []
    for inst in instances:
        items_i = _instance_items(item_rows, inst)
        # Shipment events carry no timeline key, so they are ignored when timelines collide.
        shipment_i, shipment_ids_i = _shipment(
            inst.row, None if inst.collision else shipment_evidence, items_i,
            inst.start, inst.end)
        payment_i, payment_refs_i = _payment(payment_evidence, refund_evidence, items_i, inst)
        support = _supported(topic, inst.row, shipment_i, payment_i, payment_evidence)
        scored.append(((support is True, -abs(inst.lag - _EXPECTED_LAG), inst.start,
                        inst.index), inst, items_i, shipment_i, shipment_ids_i,
                       payment_i, payment_refs_i))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    _, chosen, items, shipment, shipment_ids, payment, payment_refs = scored[0]
    order = chosen.row
    if order_row and (chosen.collision or order.get("order_purchase_timestamp")
                      != order_row.get("order_purchase_timestamp")):
        output["data_conflicts"].append({
            "field": "order_purchase_timestamp", "sources": ["order", "customer_history"],
            "selected_source": "customer_history", "resolution_code": "CASE_TIME_WINDOW",
        })
    output["affected_entities"] = {
        "order_ids": [order_id],
        "item_ids": _unique([row.get("order_item_id") for row in items]),
        "seller_ids": _unique([row.get("seller_id") for row in items]),
        "payment_references": payment_refs, "shipment_ids": shipment_ids,
    }
    output["shipment_analysis"] = shipment
    output["payment_analysis"] = payment
    work.event("handoff", "conflict-resolver", target="coordinator",
               decision_code="TIMELINE_SELECTED",
               attributes={"timelines": len(instances), "collision": chosen.collision})

    work.event("task_assigned", "coordinator", target="policy-conflict")
    policy_evidence = await work.fetch("get_policy", "policy-conflict",
                                       policy_version=case.get("policy_version", ""))
    rules = _obj(_obj(policy_evidence.get("data") if policy_evidence else None).get("rules"))
    supported = _supported(topic, order, shipment, payment, payment_evidence)
    decision_topic = topic if supported else _alternative_issue(
        order, shipment, payment, payment_evidence
    )
    if topic == "unsupported_claim" and supported:
        decision_topic = topic
    rule = _obj(rules.get(decision_topic))
    if decision_topic and rule and payment_evidence and policy_evidence:
        amount = _money(rule.get("refund_brl")) or Decimal(0)
        refundable = _money(payment.get("refundable_total_brl"))
        if refundable is not None:
            amount = min(amount, refundable)
        output["assessment"] = {
            "primary_issue": decision_topic, "secondary_issues": [],
            "case_status": rule.get("case_status", "needs_investigation"),
            "confidence": (0.9 if chosen.collision else 0.95) if supported else 0.7,
        }
        parties = _rows(rule.get("responsible_parties"))[:5]
        if shipment["late_seller_ids"]:
            parties = [
                {"party_type": party.get("party_type"),
                 "party_id": shipment["late_seller_ids"][0]
                 if party.get("party_type") == "seller" else party.get("party_id")}
                for party in parties
            ]
        elif any(party.get("party_type") == "seller" for party in parties):
            seller_ids = output["affected_entities"]["seller_ids"]
            if seller_ids:
                parties = [
                    {"party_type": party.get("party_type"),
                     "party_id": seller_ids[0] if party.get("party_type") == "seller"
                     else party.get("party_id")}
                    for party in parties
                ]
        output["root_cause_analysis"] = {
            "ranked_causes": [{"cause_code": decision_topic.upper(), "rank": 1}],
            "responsible_parties": parties,
        }
        action = rule.get("recommended_action")
        output["resolution_actions"] = [action] if isinstance(action, str) and action else []
        output["financial_resolution"] = {
            "currency": "BRL", "recommended_refund_brl": _number(amount),
            "refund_lines": [{"reason_code": decision_topic, "amount_brl": _number(amount),
                              "entity_id": order_id}] if amount > 0 else [],
        }
        work.event("policy_decided", "policy-conflict", decision_code=decision_topic.upper())
    elif supported is False and topic:
        output["assessment"] = {
            "primary_issue": "unsupported_claim", "secondary_issues": [],
            "case_status": "no_action", "confidence": 0.75,
        }
        output["resolution_actions"] = ["document_no_action"]
        work.event("policy_decided", "policy-conflict", decision_code="CLAIM_UNSUPPORTED")
    else:
        output["assessment"]["confidence"] = 0.25
        work.event("policy_decided", "policy-conflict", decision_code="EVIDENCE_INCOMPLETE")

    primary_refs = work.refs("get_customer_history", "get_order", "get_order_items",
                             "get_sellers",
                             "get_shipment_summary", "get_payment_timeline",
                             "get_refund_timeline", "get_policy")
    output["claim_assessments"] = []
    for claim in claims:
        if claim.get("topic") == "requested_full_refund":
            verdict = "unsupported" if output["assessment"]["case_status"] == "no_action" else (
                "supported" if decision_topic in {"canceled_order_paid", "unavailable_order_paid"}
                and output["financial_resolution"]["recommended_refund_brl"] > 0 else (
                    "partially_supported" if output["financial_resolution"][
                        "recommended_refund_brl"] > 0 else "insufficient_evidence"
                )
            )
            refs = work.refs("get_payment_timeline", "get_refund_timeline", "get_policy")
        else:
            verdict = "supported" if supported else (
                "unsupported" if supported is False else "insufficient_evidence"
            )
            if topic == "unsupported_claim" and supported:
                verdict = UNSUPPORTED_CLAIM_VERDICT
            refs = primary_refs
        output["claim_assessments"].append({
            "claim_id": claim["claim_id"], "verdict": verdict,
            "confidence": output["assessment"]["confidence"], "evidence_refs": refs,
        })
    output["evidence_refs"] = work.refs()
    work.event("handoff", "policy-conflict", target="verifier")
    _verify_output(work, output)
    work.event("verification_completed", "verifier", decision_code="CHECKED")
    return output
