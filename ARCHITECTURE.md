# L3B Architecture Record

Thiết kế tương ứng với `src/student_agent/workflow.py`. Tài liệu chỉ ghi quyết định và sự kiện quan sát được; không chứa key hay nội dung suy luận riêng.

## 1. System overview

Vẽ hoặc mô tả luồng từ input/candidate resolution đến MCP investigation, specialist agents, conflict resolver, verifier, output và trace.

```text
Input → Entity Resolver → Coordinator → Specialists → Conflict Resolver → Verifier → Output
            │                              │                  │             │
            └──────────────────────────── MCP ────────────────┴──────────── Trace
```

`day09 run` nạp từng case, tạo `case_received`, rồi gọi `solve_case`. Gateway discovery một lần mỗi session và chỉ gọi tool có trong catalog. Mỗi MCP response được validate theo evidence envelope; `evidence_ref` được giữ nguyên và ghi `tool_result_consumed`. Coordinator giao việc cho các specialist theo thứ tự để giữ rõ nguồn của từng quyết định. Verifier tạo output theo schema L3B V2; CLI validate trước khi ghi file và thêm `case_finalized`.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| Entity/customer | Case, claimed ID, candidates, customer hint, `opened_at` | Chọn bản ghi order thuộc thời điểm khiếu nại, loại candidate không hợp lệ | `get_customer_history`, `get_order` | Order ID đã resolve, khoảng thời gian giao dịch, rejected candidates |
| Coordinator | Case và kết quả specialist | Giao việc, gom bằng chứng theo case | Không gọi tool nghiệp vụ trực tiếp | `task_assigned`, `handoff`, output tổng hợp |
| Order/product | Resolved order ID | Lấy item, seller ID, giá và bối cảnh sản phẩm | `get_order_items`, `get_product_context` | Item/seller IDs và expected total |
| Shipment | Resolved order và khoảng thời gian | So delivered vs estimated, carrier vs shipping limit | `get_shipment_summary`; `get_sellers` chỉ khi seller delay | Shipment verdict, late seller IDs |
| Payment/refund | Resolved order và khoảng thời gian | Tổng hợp capture/refund event, phát hiện mismatch/duplicate | `get_payment_timeline`; `get_refund_timeline` theo topic | Payment verdict và totals |
| Policy | `policy_version`, issue đã kiểm tra | Chọn rule, action và refund tối đa theo capture còn lại | `get_policy` | `policy_decided`, financial resolution |
| Conflict resolver | Customer history, order row, shipment row | Phát hiện order/timestamp/status khác nhau; ưu tiên bản ghi phù hợp thời điểm case | Không gọi thêm tool | `data_conflicts` và resolution code |
| Verifier | Evidence và các kết quả | Giới hạn output theo schema, không tạo ref mới, kiểm tra case scope | Không gọi tool | `verification_completed`, output cuối |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool.

## 3. Entity resolution và A2A protocol

Candidate có dạng order ID 32 ký tự hex mới được truy vấn; placeholder `candidate-*` bị reject mà không tốn MCP call. `get_customer_history` cung cấp các order row cùng customer hint. Khi có nhiều row trùng order ID, resolver chỉ xét giao dịch không sau `opened_at`, ưu tiên row có trạng thái/timeline độc lập xác nhận topic (canceled, unavailable hoặc late delivery), rồi dùng purchase timestamp để phân xử. Lần mua kế tiếp của cùng ID đóng vai trò cận trên để lọc item, shipment, payment và refund timeline. Nếu history thiếu, `get_order` chỉ được dùng khi timestamp không sau `opened_at`; nếu vẫn thiếu thì `not_found` và confidence thấp. Không suy ra ID từ tên candidate.

A2A được thể hiện bằng `task_assigned` từ coordinator và `handoff` từ entity/specialist về coordinator hoặc verifier. Mọi event có `case_id`; workflow xử lý tuần tự, không có vòng lặp giữa agent. Một tool lỗi được bỏ qua cho case đó, không retry vô hạn.

## 4. Evidence và conflict lifecycle

Gateway validate `schema_version`, `evidence_ref`, `result_hash`, `domain` và `data` trước khi trả. `solve_case` giữ evidence trong dict riêng cho từng case, emit `tool_result_consumed` ngay sau call thành công, rồi chỉ xuất các ref đã nhận trong cùng case. Claim assessments, quyết định policy và verifier dùng cùng ref set. Không có cache xuyên case hay ref tự tạo.

Nếu `get_order` và customer history khác timestamp, hoặc shipment status khác bản ghi đã chọn, output thêm `data_conflicts` với `selected_source=get_customer_history` và `resolution_code=TEMPORAL_CASE_MATCH`. Timeline event ngoài khoảng giao dịch được loại khỏi tính toán. Nếu evidence không đủ, output dùng `insufficient_evidence`/`needs_investigation`, không tạo amount hay kết luận từ claim đơn thuần.

Nếu item rows trùng hoàn toàn, tổng giá chỉ tính một lần. Với split payment, khi gateway trả cả một capture lạc nhóm cùng hai capture khớp tổng item + freight, resolver dùng cặp khớp và ghi `RECONCILED_SPLIT_SUBSET` trong `data_conflicts`; bản ghi capture thừa không được tính vào `captured_total_brl`.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| MCP timeout/lỗi tool | 0 retry trong một run | Thiếu domain đó; chỉ kết luận nếu evidence còn đủ | Warning log; không emit `tool_result_consumed` |
| Entity not found/ambiguous | Tối đa 1 call/customer và 1 call/claimed order | `not_found`, empty entity IDs, confidence 0.1 | `handoff` với `ORDER_NOT_FOUND` |
| Source conflict | Không gọi thêm để quét rộng | Chọn row khớp thời điểm case; ghi conflict | `data_conflicts`, `TEMPORAL_CASE_MATCH` |
| Invalid specialist result | Gateway schema validation; 0 retry | Bỏ result, giảm mức chắc chắn | Warning log; verifier code `NO_EVIDENCE` nếu cần |

Budget mục tiêu: 1 call entity bằng `get_customer_history`; `get_order` chỉ là fallback khi history không resolve được claimed ID. Sau đó có 3 call specialist cơ bản, 1 call product theo scope và 1 call policy; refund timeline chỉ cho `refund_pending`/`refund_failed`, seller chỉ cho seller delay. Trong run đầu, `get_refund_timeline` trả lỗi ở mọi case canceled/unavailable không có refund event, nên bỏ 20 call thừa đó ở các run tiếp theo. Không gọi candidate placeholder, không gọi `get_order_payments` vì payment timeline đã chứa payment rows. Catalog MCP được cache trong session; evidence chỉ cache trong phạm vi một case. Không có retry tự động, tránh call dư được audit.

## 6. Verification invariants

- CLI kiểm tra output bằng JSON Schema L3B V2 và đúng `case_id` trước khi ghi.
- Resolved ID phải thuộc candidate thật; placeholder bị reject và không được gọi MCP.
- Item, shipment, payment và refund được lọc vào giao dịch đã chọn; event trước purchase hoặc sau lần mua tiếp theo không được tính.
- `captured_total_brl` tính từ confirmed capture, `refunded_total_brl` từ confirmed refund, `recommended_refund_brl` không vượt số tiền còn có thể hoàn và bằng 0 nếu không có action.
- Với đơn canceled/unavailable, confirmed capture đủ để payment được `reconciled`; chênh lệch với item + freight không được biến capture thật thành thiếu evidence. Refund vẫn bị chặn bởi policy và refundable balance.
- Claim, issue, responsible party và action được chọn từ evidence cùng policy version; bất đồng nguồn được ghi trong `data_conflicts`.
- Mọi `evidence_ref` trong output xuất phát từ response MCP của case đó và có event `tool_result_consumed`; confidence luôn trong [0, 1].

## 7. Reproducibility

Workflow là rule-based, không dùng LLM hay random seed. Python >=3.11; dependency ranges nằm trong `pyproject.toml`, contract version trong `contracts/`. Chạy một case tại một thời điểm và một MCP session cho toàn bộ run. Lệnh: `python -m pip install -e ".[dev]"`, `day09 validate-inputs`, `day09 mcp-tools`, `day09 run`, `day09 validate`, `pytest -q`. `.env` giữ local và bị Git ignore; không ghi Team API Key vào source, output hay trace. Lệnh đóng gói/nộp bài không nằm trong quá trình phát triển này.
