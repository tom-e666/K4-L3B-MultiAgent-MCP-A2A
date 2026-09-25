# Phân tích chiến lược từ hạng #2

## 1. Kết quả quan sát

Ảnh leaderboard ngày 25/09/2026 ghi nhận **85.0414 điểm, hạng #2**. Breakdown public:

| Thành phần | Điểm | Trọng số L3B | Đóng góp vào tổng | Khoảng còn có thể tăng |
| --- | ---: | ---: | ---: | ---: |
| Semantic | 85.57 | 40% | 34.23 | 5.77 |
| Evidence coverage | 84.27 | 15% | 12.64 | 2.36 |
| MCP provenance | 93.60 | 15% | 14.04 | 0.96 |
| Consistency | 90.48 | 10% | 9.05 | 0.95 |
| Schema | 93.60 | 5% | 4.68 | 0.32 |
| Calibration | 89.22 | 5% | 4.46 | 0.54 |
| Multi-agent workflow | 93.60 | 5% | 4.68 | 0.32 |
| Tool-call efficiency | 25.27 | 5% | 1.26 | 3.74 |
| **Tổng** |  |  | **85.04** | **14.96** |

Phép cộng dùng đúng trọng số trong `contracts/scoring/scoring-policy-v2.json`. Public score chỉ phản ánh public partition; private chiếm 80% kết quả cuối, vì vậy không nên tối ưu bằng cách hard-code theo 100 case hiện tại.

## 2. Dấu hiệu trong artifact hiện tại

Lượt local gần nhất có 100 outputs, 2.224 trace events và 762 `tool_result_consumed`. Vì bốn case đã được chạy lại và một case bị dừng giữa chừng trước đó, trace hiện có 105 `case_received` và 104 `case_finalized`; đây là artifact phát triển, không phải trace sạch để đóng gói.

Các tool thành công xuất hiện trong trace:

| Tool | Số call thành công | Nhận xét |
| --- | ---: | --- |
| `get_customer_history` | 105 | Cần cho temporal entity resolution |
| `get_order` | 105 | Phần lớn trùng thông tin đã có trong customer history |
| `get_order_items` | 105 | Cần cho item, seller và tổng tiền kỳ vọng |
| `get_shipment_summary` | 104 | Cần cho shipment verdict và source conflict |
| `get_payment_timeline` | 104 | Cần vì mọi case đều có claim hoàn tiền |
| `get_product_context` | 104 | Scope yêu cầu product context, nhưng không phải claim nào cũng cần ref này |
| `get_policy` | 104 | Cần cho action và refund policy |
| `get_refund_timeline` | 20 | Chỉ nên dùng cho `refund_pending`/`refund_failed` |
| `get_sellers` | 11 | Chỉ dùng khi đã phát hiện seller delay |

Mỗi output chứa trung bình 7.3 evidence refs. Hiện `claim_assessments[*].evidence_refs` nhận toàn bộ ref của case, kể cả domain không liên quan đến claim. Cách này tăng recall nhưng làm giảm evidence precision, phù hợp với evidence coverage chỉ đạt 84.27.

Confidence cũng đang gom thành hai mức: 0.82 cho 90 case và 0.65 cho 10 case. Điều này chưa phản ánh mức đầy đủ evidence, conflict hay fallback của từng case.

## 3. Ưu tiên cải thiện

### P0 — Tạo một run sạch trước mọi lần đo

Chạy lại từ đầu trong **một MCP session**, không dùng `--resume` qua session khác và không `--force-case` trước khi đóng gói. Mục tiêu là mỗi case có đúng một lifecycle:

```text
case_received → task_assigned/tool_result_consumed/handoff
→ policy_decided → verification_completed → case_finalized
```

Lý do: provenance yêu cầu evidence ref khớp team, run và case. Trace phát triển hiện chứa lifecycle lặp cho một số case; output mới có thể tham chiếu evidence của lần chạy sau trong khi trace vẫn chứa refs của lần chạy trước. Một run sạch cũng giúp workflow ordering và số call dễ audit.

Điều kiện chấp nhận:

- đúng 100 `case_received`, 100 `verification_completed`, 100 `case_finalized`;
- mỗi output ref có đúng một `tool_result_consumed` cùng case;
- không có output ref từ session trước;
- `day09 validate` và test đều đạt.

### P1 — Bỏ `get_order` khỏi happy path

`get_customer_history` đã cung cấp order row, timestamp, trạng thái và customer identity. Chỉ gọi `get_order` khi history không resolve được candidate, hoặc khi verifier xác định thiếu một field bắt buộc. Điều này có thể tiết kiệm gần **1 call/case** mà không giảm evidence coverage của luồng bình thường.

Không bỏ `get_product_context` ngay: input đặt `include_product_context=true` cho toàn bộ 100 case, nên việc bỏ nó có nguy cơ làm giảm evidence coverage/private score. Trước tiên chỉ giảm call rõ ràng bị trùng là `get_order`.

Kỳ vọng: efficiency là cơ hội tăng lớn nhất. Nếu efficiency tăng từ 25.27 lên 70, tổng điểm tăng khoảng **2.24 điểm**; lên 90 thì tăng khoảng **3.24 điểm**. Đây là ước tính theo trọng số, không phải dự đoán scorer.

### P1 — Gắn evidence theo claim và theo field

Tạo registry trong một case:

```text
entity_refs  = customer_history (+ order fallback)
order_refs   = order_items + product_context
shipment_refs = shipment_summary (+ sellers khi seller delay)
payment_refs = payment_timeline (+ refund_timeline khi cần)
policy_refs  = policy
```

Sau đó map refs tối thiểu đủ chứng minh từng claim:

| Claim | Evidence nên gắn |
| --- | --- |
| Late delivery logistics | entity + shipment + policy |
| Late delivery seller | entity + shipment + items/seller + policy |
| Valid split payment | entity + items + payment + policy |
| Payment mismatch / duplicate | entity + items + payment + policy |
| Refund pending / failed | entity + payment + refund + policy |
| Canceled/unavailable paid | entity + items + payment + policy |
| Unsupported claim | refs trực tiếp phủ định claim; không đính kèm mọi domain |
| Requested full refund | payment/refund + policy + refs của primary issue |

Top-level `evidence_refs` vẫn là union duy nhất của refs thực sự dùng. Không đưa product ref vào claim shipment nếu product không tham gia kết luận. Mục tiêu là tăng evidence precision mà vẫn giữ recall.

### P1 — Sửa thứ tự lifecycle của specialist

Một số call bổ sung đang diễn ra sau `handoff` của chính actor:

- `get_product_context` sau handoff của order agent;
- `get_refund_timeline` sau handoff của payment agent;
- `get_sellers` sau handoff của shipment agent;
- `policy_decided` sau handoff từ policy agent sang verifier.

Mỗi actor nên hoàn thành mọi call được phép, emit các `tool_result_consumed`, rồi mới handoff. `policy_decided` phải đứng trước handoff sang verifier. Việc này có thể đưa workflow từ 93.60 gần 100 mà không thêm call.

### P2 — Làm confidence theo chất lượng evidence

Thay hai mức cố định bằng rule có thể kiểm chứng:

| Tình trạng | Confidence đề xuất |
| --- | ---: |
| Entity, domain evidence và policy đầy đủ; không conflict unresolved | 0.92–0.96 |
| Có conflict nhưng đã resolve bằng source precedence | 0.82–0.90 |
| Thiếu một domain không quyết định primary issue | 0.65–0.78 |
| Entity ambiguous hoặc evidence thiếu để kết luận | 0.20–0.45 |

Confidence của từng claim cũng phải dựa trên refs riêng của claim. Không tăng confidence chỉ vì topic input trùng với kết luận; input là lời khiếu nại, không phải ground truth.

### P2 — Tăng semantic và consistency bằng invariant

Giữ các invariant đã có và bổ sung kiểm tra trước finalize:

- `secondary_issues` phản ánh issue có evidence độc lập, thay vì luôn để trống;
- `requested_full_refund` chỉ `supported` khi policy cho phép toàn bộ refundable amount; nếu chỉ hoàn freight thì `partially_supported`;
- `no_action` luôn có refund 0 và không có refund line;
- seller responsibility chỉ xuất hiện khi seller delay và seller ID có evidence;
- `captured_total_brl`, `refunded_total_brl`, `refundable_total_brl` tuân theo `captured - refunded`;
- không dùng topic trong input để chọn transaction trừ khi customer history/timeline có dấu hiệu độc lập xác nhận topic;
- conflict đã resolve phải chỉ rõ source được chọn; conflict chưa resolve phải hạ confidence và dùng `needs_investigation`.

### P2 — Điều tra Schema/MCP provenance 93.60

JSON Schema local đã pass 100 outputs, nên mức 93.60 có thể đến từ case-level artifact/provenance hoặc quy tắc scorer rộng hơn JSON Schema công khai. Không nên đoán private oracle. Sau clean run, kiểm tra:

- số claim assessments bằng số claim input và giữ nguyên `claim_id`;
- không có refs không được consumed;
- không có event sau `case_finalized` cùng case;
- resolved/rejected candidates không overlap;
- danh sách ID unique và không chứa placeholder;
- output/trace chỉ thuộc đúng run vừa tạo.

Nếu clean run vẫn 93.60, cần dùng aggregate feedback của submission kế tiếp để phân biệt lỗi schema thực tế với hard-gate/provenance penalty.

## 4. Kế hoạch thí nghiệm

Không thay nhiều nhóm logic trong cùng một submission. Mỗi phương án phải chạy mới hoàn toàn và giữ log local riêng để so sánh.

| Vòng | Thay đổi | Metric mục tiêu | Guardrail |
| --- | --- | --- | --- |
| A | Clean run, không sửa semantic | Provenance/schema/workflow | Semantic và evidence không giảm |
| B | `get_order` chỉ fallback | Efficiency | Entity resolution, provenance không giảm |
| C | Evidence refs theo claim | Evidence coverage | Không thiếu required evidence group |
| D | Sửa lifecycle ordering | Workflow | Không tăng tool calls |
| E | Confidence theo evidence tiers | Calibration | Semantic/consistency giữ nguyên |
| F | Secondary issue + invariant tài chính | Semantic/consistency | Schema 100 local |

Mỗi vòng ghi lại: commit SHA, thời gian run, số successful/failed MCP calls theo tool, event counts, validation result và public component breakdown. Chỉ so các submission tạo từ cùng code và một clean run.

## 5. Mục tiêu thực tế cho vòng kế tiếp

Mục tiêu ưu tiên là tăng điểm mà ít rủi ro private overfit:

| Metric | Hiện tại | Mục tiêu vòng kế | Lý do |
| --- | ---: | ---: | --- |
| Efficiency | 25.27 | ≥70 | Bỏ call trùng, không mất evidence chính |
| Evidence | 84.27 | ≥88 | Claim-scoped refs tăng precision |
| Provenance | 93.60 | ≥98 | Một run sạch, refs cùng scope |
| Workflow | 93.60 | ≥98 | Lifecycle ordering đúng |
| Consistency | 90.48 | ≥93 | Thêm invariant tài chính/action |
| Calibration | 89.22 | ≥91 | Confidence theo evidence |
| Semantic | 85.57 | ≥87 | Secondary issue và claim verdict chính xác hơn |

Nếu đạt các mức này, tổng theo trọng số xấp xỉ **89.7**. Đây là target kỹ thuật để định hướng, không phải cam kết leaderboard. Ưu tiên đầu tiên là P0 + P1 vì vừa có mức tăng tiềm năng lớn, vừa ít phụ thuộc private data.

## 6. Những việc không nên làm

- Không dùng topic input như nhãn đúng để ép `primary_issue`.
- Không gọi tất cả tool cho mọi case chỉ để tăng số evidence refs.
- Không gắn toàn bộ refs vào mọi claim.
- Không resume artifact dùng để chấm qua nhiều MCP session.
- Không tối ưu theo một vài case public bằng hard-coded case ID, amount hoặc timestamp.
- Không tạo/sửa `evidence_ref`, không dùng ref chéo case và không bỏ validation trước finalize.
- Không chọn final chỉ dựa trên tổng public; cần ưu tiên logic có khả năng tổng quát vì private chiếm 80%.

## 7. Implementation cuối cho nhóm canceled order paid

Phân tích thủ công các case `008, 018, 028, 038, 048, 058, 068, 078, 088, 098` cho thấy cùng một mẫu: customer history xác nhận order đã hủy, payment timeline có capture `79.0 BRL`, còn item + freight là `97.0 BRL`. Logic cũ yêu cầu capture khớp tổng item + freight nên trả `payment.verdict = insufficient_evidence`, dù capture và policy refund đều rõ ràng.

Bản sửa áp dụng quy tắc tổng quát theo issue thay vì hard-code case ID hoặc amount: với `canceled_order_paid`/`unavailable_order_paid`, một confirmed capture không duplicate và không mismatch là đủ để payment `reconciled`; refund vẫn lấy từ policy và không vượt refundable balance. Targeted run trên cả 10 case đều cho `captured=79.0`, `recommended_refund=79.0`, payment `reconciled`; mỗi claim dùng 4 refs liên quan thay vì toàn bộ 6 refs của case.

Workflow đồng thời chuyển `get_order` thành fallback sau customer history và đặt mọi specialist handoff sau call cuối của actor. Unit test khóa regression cho reconciliation, scoped evidence và thứ tự `policy_decided → handoff → verification_completed`.

Lượt clean run 100 case ngày 2026-09-25 bị dừng ở case 59 vì MCP gateway bắt đầu lỗi liên tục từ case 45. Artifact dở dang này không được dùng để đóng gói hoặc nộp; cần chạy lại từ đầu khi gateway ổn định.
