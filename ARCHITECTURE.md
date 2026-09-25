# L3B Architecture Record

Tài liệu mô tả các quyết định có thể kiểm chứng trong `src/student_agent/workflow.py`. Không chứa prompt hay chain-of-thought. Workflow hoàn toàn deterministic, không gọi LLM.

## 1. System overview

```text
case input
   │
   ▼
Coordinator ──task_assigned──► Entity/customer ──(get_customer_history)──► resolve order + timelines
   │                                                                        │ handoff ENTITY_RESOLVED
   ├──task_assigned──► Order/product ──(get_order, get_order_items | get_product_context)
   ├──task_assigned──► Shipment ──(get_shipment_summary, only for delivery topics)
   ├──task_assigned──► Payment/refund ──(get_payment_timeline, get_refund_timeline for refund topics)
   ├──task_assigned──► Conflict resolver ── choose the timeline the complaint refers to (no MCP call)
   ├──task_assigned──► Policy ──(get_policy) ── decision, refund, responsible parties
   └──────────────────► Verifier ── schema + invariants ── verification_completed CHECKED
                                                                          │
                                                   outputs/<case_id>.json + traces/trace.jsonl
```

Every MCP result goes through `EvidenceGateway`: JSON Schema validation (`mcp-evidence-response-v1`), then it is cached per case and a `tool_result_consumed` event is emitted with the `evidence_ref`.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | `customer_unique_id_hint`, `candidate_order_ids`, `opened_at` | Lấy lịch sử khách, lọc candidate có trong lịch sử và mua trước `opened_at`, liệt kê các timeline của order | `get_customer_history` | `entity_resolution`, `customer_context`; handoff `ENTITY_RESOLVED` / `ENTITY_UNRESOLVED` |
| Coordinator | case | Giao việc theo topic của claim, quyết định tool nào cần cho topic đó | none | `task_assigned`, `case_received`, `case_finalized` |
| Order/product | resolved order id | Dữ liệu order, item/seller (items cho topic thanh toán, product context cho topic giao hàng/refund) | `get_order`, `get_order_items`, `get_product_context` | `affected_entities` |
| Shipment | order + items | Verdict giao hàng: `on_time` / `seller_delay` / `logistics_delay` / `lost` / `returned`; seller trễ theo `shipping_limit` | `get_shipment_summary` (chỉ late_delivery_* và unsupported_claim) | `shipment_analysis` |
| Payment/refund | order + timeline window | Capture, refund, refundable; verdict `reconciled` / `capture_mismatch` / `duplicate_capture` / `refund_pending` / `refund_failed` | `get_payment_timeline`, `get_refund_timeline` (chỉ refund_*) | `payment_analysis` |
| Policy | topic + analyses | Kiểm tra claim có được evidence hỗ trợ; chọn rule, giới hạn refund bằng refundable, thay seller chịu trách nhiệm bằng seller thật của đơn | `get_policy` | `assessment`, `root_cause_analysis`, `financial_resolution`, `resolution_actions`, `claim_assessments` |
| Conflict resolver | các timeline cùng `order_id` | Chọn timeline mà complaint nhắc đến; tách dữ liệu khi hai timeline trùng thời điểm | none | `data_conflicts`; handoff `TIMELINE_SELECTED` |
| Verifier | draft output | Kiểm tra schema và invariants ở mục 6 | none | `verification_completed` `CHECKED` / `INSUFFICIENT_EVIDENCE` |

Least privilege: mỗi actor chỉ gọi tool của domain mình. Tool discovery không có nghĩa là gọi mọi tool; tool nào gọi được quyết định theo topic (mục 5).

## 3. Entity resolution và A2A protocol

- **Candidate ranking.** Candidate phải xuất hiện trong lịch sử khách hàng và có `order_purchase_timestamp ≤ opened_at`. Candidate không có trong lịch sử (vd. `candidate-001`) bị đưa vào `rejected_candidates`.
- **Nhiều timeline cùng order id.** Một `order_id` có thể có nhiều dòng lịch sử, tức nhiều lần mua. Mỗi dòng là một timeline, với cửa sổ thời gian từ lúc mua đến lần mua kế tiếp. Timeline được chọn theo thứ tự: (1) gần nhất với độ trễ khiếu nại quan sát được (12 ngày trước `opened_at`), (2) timeline mà evidence hỗ trợ claim, (3) mới hơn.
- **Timeline trùng thời điểm.** Khi hai dòng có cùng thời điểm mua, dữ liệu được tách theo thứ tự dòng: nhóm payment được nhận ra khi `payment_sequential` quay về 1, item lấy theo index. Shipment event không có khoá timeline nên bị bỏ qua trong trường hợp này.
- **Confidence.** Entity resolved: 0.95. Không resolve được: 0.15, output `insufficient_evidence`, không gọi thêm tool.
- **Message envelope.** Mọi event dùng `case_id` làm correlation id. Handoff chỉ đi theo hướng specialist → coordinator → verifier, không có vòng lặp. Trace chỉ ghi sự kiện quan sát được: actor, target, decision code, tool, evidence refs.

## 4. Evidence và conflict lifecycle

1. `EvidenceGateway.call` validate response với schema, lỗi tool (`is_error`) được raise thành `RuntimeError`.
2. `Investigation.fetch` cache kết quả theo `(tool, arguments)` trong phạm vi một case, nên không bao giờ gọi trùng. Mỗi kết quả emit `tool_result_consumed` kèm `evidence_ref`.
3. Chọn source: dòng lịch sử khách hàng của timeline đã chọn là nguồn authoritative. `get_order` trả về lần mua mới nhất của order id, nên khi lệch với timeline đã chọn thì ghi `data_conflicts` với `selected_source = customer_history` và `resolution_code = CASE_TIME_WINDOW`.
4. Mapping evidence: `evidence_refs` của output chỉ trích các nguồn hỗ trợ kết luận chính của topic. Với `late_delivery_*` và `unsupported_claim`, payment timeline vẫn được gọi để điền `payment_analysis` nhưng không được trích; với `unsupported_claim` shipment summary cũng không được trích vì kết luận "giao đúng hạn" dựa trên ngày giao/ngày dự kiến của timeline đã chọn (`UNCITED_TOOLS`). Các lời gọi đó vẫn nằm trong trace. `claim_assessments[].evidence_refs` luôn là tập con của `evidence_refs`.
5. Evidence không dùng chéo case: verifier kiểm tra mọi ref thuộc tập ref của case đó.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout / rớt kết nối | 6 lần kết nối lại, backoff 5→60 s (`cli.py`) | Chạy tiếp từ case chưa xong; `day09 run --resume` chỉ chạy lại case chưa hoàn tất | stderr `Retry n/6` |
| Entity not found/ambiguous | 0 | Output `insufficient_evidence`, confidence 0.1, không gọi tool khác | `handoff ENTITY_UNRESOLVED`, `verification_completed INSUFFICIENT_EVIDENCE` |
| Source conflict | 0 | Chọn timeline theo mục 3, ghi `data_conflicts` | `handoff TIMELINE_SELECTED` |
| Invalid specialist result | 0 | Verifier raise, case không được ghi | (case không finalize) |

**Query budget.** Mỗi case dùng tối đa 6 lời gọi:

| Topic | Tools |
| --- | --- |
| late_delivery_logistics, late_delivery_seller | history, order, product, shipment, payment, policy |
| unsupported_claim | history, order, product, shipment, payment, policy |
| refund_pending, refund_failed | history, order, product, payment, refund, policy |
| valid_split_payment, payment_mismatch, duplicate_charge, canceled_order_paid, unavailable_order_paid | history, order, items, product, payment, policy |

Không gọi `get_sellers` (seller id đã có trong item/product). Không gọi shipment cho topic thanh toán. Mọi call đều có cache và không retry trong phạm vi case.

## 6. Verification invariants

Được kiểm tra trong `_verify_output` trước khi CLI ghi output:

- Output đúng `l3b-output-v2` schema; `case_id` không đổi.
- `resolved_order_ids` và `rejected_candidates` không giao nhau.
- Mọi `evidence_refs` thuộc tập ref đã nhận trong case; ref của claim là tập con của ref output.
- Tổng `refund_lines` bằng `recommended_refund_brl`.
- `no_action` thì không đề xuất refund; refund không vượt `refundable_total_brl`.
- `action_required` phải có evidence.
- Seller chịu trách nhiệm là seller thật của đơn (seller trễ hạn gửi, hoặc seller của item).
- Confidence nằm trong [0, 1]: 0.99 khi claim được evidence hỗ trợ, 0.7 khi dùng issue thay thế, 0.75 khi claim không được hỗ trợ.

## 7. Reproducibility

- Không dùng model/LLM; logic deterministic, không có random seed.
- Python ≥ 3.11; dependencies trong `pyproject.toml` (`mcp>=2,<3`, `httpx2`, `jsonschema[format]`, `python-dotenv`).
- Chạy tuần tự từng case (concurrency 1, nghỉ 1 s giữa các case).
- Lệnh: `.\scripts\run_submit.ps1` (gồm `day09 run` → `day09 validate` → `day09 package --output dist/submission.zip`).
- Các switch cấu hình nằm ở đầu `workflow.py` (`SHIPMENT_TOPICS`, `ITEM_TOPICS`, `PRODUCT_TOPICS`, `UNCITED_TOOLS`, `CONFIDENCE_SUPPORTED`, …). API key chỉ nằm trong `.env`, không có trong output hay trace.
