# L3B Architecture Record

## 1. System overview

`solve_case()` coordinates deterministic specialist roles. Every MCP response is
validated by `EvidenceGateway` against the public evidence schema. The workflow
keeps responses and refs only in memory for the current case.

```text
Input → Entity/Customer → Coordinator → Order/Product → Shipment → Payment/Refund
                                      → Policy/Conflict → Verifier → Output
                        MCP evidence → case-local store → trace + output
```

The current implementation executes specialists sequentially through one MCP
session. This avoids concurrent calls sharing a session and makes the trace
order unambiguous. Actor names represent ownership and observable handoffs;
the competition does not require a particular agent framework.

## 2. Agent ownership

| Actor | Input | Responsibility | MCP permission | Handoff |
| --- | --- | --- | --- | --- |
| `coordinator` | Case and specialist results | Assign work, combine results, keep case scope | None | Entity, specialists, verifier |
| `entity-customer` | Candidate IDs, customer hint, opened time | Match candidate to customer history and reject others | `get_customer_history` | Resolved order and time window |
| `order-product` | Resolved order, product scope | Order status, items, seller and product context | `get_order`, `get_order_items`, `get_product_context`, conditional `get_sellers` | Entities and order row |
| `shipment` | Order row and items | Timeline, seller handoff and delivery verdict | `get_shipment_summary` | Shipment verdict |
| `payment-refund` | Resolved order and claim | Captures, refund events and BRL totals | `get_payment_timeline`, conditional `get_refund_timeline` | Payment verdict and totals |
| `policy-conflict` | Specialist results and policy version | Apply issue rule, resolve source conflict, decide action/refund | `get_policy` | Draft output |
| `verifier` | Draft output and evidence refs | Check schema and cross-field invariants | None | Final output |

`get_sellers` is called for seller-responsibility claims. `get_order_payments`
is not called because payment lifecycle already includes base payment rows.
Redundant calls reduce L3B efficiency.

## 3. Entity resolution and A2A protocol

The coordinator sends each specialist a case-scoped task. Observable messages
use trace `task_assigned` and `handoff`, correlated by `case_id`; tool usage
uses `tool_result_consumed`. No private reasoning is logged.

Entity resolution retrieves scoped customer history, filters to candidate
orders with a purchase timestamp no later than `opened_at`, and selects the
nearest such row. Other candidate IDs are rejected. A later row with the same
order ID defines the end of the selected timeline window. If no candidate
matches, the workflow returns `needs_investigation` and does not invent an
order. There are no recursive handoffs or unbounded loops.

## 4. Evidence and conflict lifecycle

Each successful tool response is cached by tool name and arguments within a
single case. Its server-provided `evidence_ref` is recorded without change and
emitted in a `tool_result_consumed` event. Output and claim refs come only
from this case-local store. The selected customer-history row takes precedence
over an order row with a different purchase timestamp when the latter does
not fit the case time window. This is recorded in `data_conflicts`.

Payment and refund lifecycle events are filtered to the selected order's time
window. Refund amounts come from the relevant policy rule and are capped by
the available captured amount. A full-refund request is assessed separately
from the supported primary issue.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace/output |
| --- | ---: | --- | --- |
| MCP tool error or timeout | Up to 6 reconnects for the current case | Discard the incomplete case trace and retry with backoff | Only finalized case traces enter `trace.jsonl` |
| Broken MCP session | Up to 6 reconnects for the current case | Reconnect and continue from the failed case | Completed outputs and traces are preserved |
| Entity not found or ambiguous | 0 extra broad queries | Return insufficient evidence | `ENTITY_UNRESOLVED` |
| Source conflict | 0 automatic queries | Select row matching case window and record conflict | `data_conflicts` |
| Incomplete specialist result | 0 automatic queries | Preserve missing/insufficient verdict | Verification event |

The workflow avoids querying every candidate order: scoped customer history
resolves candidate membership first. `get_refund_timeline` is reserved for
claims where refund state affects the conclusion. The cache prevents repeated
calls with identical case-local arguments. All audited calls count toward
L3B efficiency, including calls that do not appear in output.

## 6. Verification invariants

Before finalization, validation must cover the L3B JSON schema, correct case
ID, entity scope, rejected candidates, evidence ownership and claim linkage.
It must also compare shipment dates and seller handoff, reconcile captured,
refunded and refundable totals, resolve source precedence, ensure status,
responsibility and action agree, and keep confidence between zero and one.
The verifier checks schema, case ID, candidate separation, case-local evidence
refs, claim linkage, refund-line totals, available capture and no-action/refund
consistency. The CLI validates schema and case ID again before writing each
output.

## 7. Reproducibility

- Python 3.11 or later; dependencies in `pyproject.toml` use bounded versions.
- Deterministic rules; no model, prompt, random seed or external LLM call.
- One case at a time, one MCP session, one in-memory cache per case.
- Run `day09 validate-inputs`, `day09 mcp-tools`, `day09 run`,
  `day09 validate`, then `day09 package --output dist/submission.zip`.
- Use `day09 run --resume` to repair incomplete results after a remote MCP
  failure, or `day09 run --resume --case-id ID` to revisit one case.
- Never store a Team API Key in source, output, trace or submission.
