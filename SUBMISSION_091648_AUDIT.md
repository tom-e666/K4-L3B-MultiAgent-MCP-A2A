# Audit submission-20260925T091648Z.zip

## Kết luận

Artifact có đủ manifest và 100 output hợp schema, nhưng trace và outputs không thuộc cùng một run hoàn chỉnh. Đây là nguyên nhân phù hợp với kết quả hard gate 0 điểm.

## Bằng chứng

- Trace có 1,023 events hợp schema.
- Chỉ có 60 `case_received` và 59 `case_finalized` cho 100 cases.
- Case 060 có `case_received` nhưng không có `case_finalized`.
- Case 061–100 không có lifecycle trace nào.
- Toàn bộ evidence refs của outputs 060–100 không có `tool_result_consumed` tương ứng.
- Case 046–059 được tạo lúc MCP lỗi: entity `not_found`, evidence refs rỗng và output chỉ khoảng 1.2 KB.
- Không phát hiện evidence ref dùng chéo giữa các case.

## Nguyên nhân

Lượt clean run bị gateway lỗi từ case 45 và dừng khi vừa bắt đầu case 060. Outputs 060–100 còn sót từ một lượt khác. Lệnh package trước đây chỉ validate JSON Schema và inventory nên đã đóng gói artifact trộn run.

## Khắc phục

Validator nay yêu cầu mỗi case có đúng một `case_received`, đúng một `case_finalized`, lifecycle đúng thứ tự và mọi output evidence ref phải có `tool_result_consumed` trong cùng case. MCP tool call có tối đa một retry với jitter 150–450 ms cho lỗi transient; kết luận nghiệp vụ vẫn deterministic.

Phải chạy mới toàn bộ 100 case trong một MCP session. Không resume hoặc đóng gói artifact hiện tại.
