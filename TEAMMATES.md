# TEAMMATES — Day 09 Multi-Agent MCP + A2A (L3B)

## Thành viên và đóng góp

| STT | Thành viên | MSSV | GitHub | Vai trò | Đóng góp trong bài L3B |
| ---: | --- | --- | --- | --- | --- |
| 1 | **Thái Phúc Tiến** | `2A202602873` | [`tom-e666`](https://github.com/tom-e666) | Trưởng nhóm, tích hợp hệ thống | Tích hợp workflow điều tra 100 case; temporal entity resolution; reconciliation payment/refund; điều phối specialist agents; tối ưu MCP calls; tổng hợp chiến lược và sửa các edge case `canceled_order_paid`. |
| 2 | **Nguyễn Đức Long** | `2A202602917` | [`duclongt23`](https://github.com/duclongt23) | MCP và evidence pipeline | Phân tích contract MCP; thiết kế tool discovery, fallback và evidence envelope; rà soát `evidence_ref`, provenance theo case, trace `tool_result_consumed`; đề xuất giảm call thừa và retry có jitter cho lỗi gateway tạm thời. |
| 3 | **Nguyễn Thành Luân** | `2A202602769` | — | Contract test và release QA | Kiểm tra JSON Schema, inventory 100 outputs và trace lifecycle; rà soát packaging V2, timestamp, secret leakage và release safety; chạy test/validation, đối chiếu artifact với hard gates trước khi đóng gói. |
| 4 | **Trần Đình Duy** | `2A202602631` | [`Duytd26`](https://github.com/Duytd26) | Error analysis và báo cáo | Phân tích kết quả grader/leaderboard; rà soát 10 edge case có đuôi `8`; đối chiếu conflict order/payment; tổng hợp audit submission 0 điểm, failure modes và khuyến nghị cho vòng chạy sạch tiếp theo. |

## Ma trận bằng chứng trong repository

| Hạng mục | Thành viên tham gia | File/commit đối chiếu |
| --- | --- | --- |
| Workflow multi-agent, entity resolution, payment reconciliation | Tiến, Long | `src/student_agent/workflow.py`, `src/student_agent/mcp_gateway.py`, commit `1cf6913`, `1b82b5c` |
| MCP provenance, evidence scoping và trace lifecycle | Long, Luân, Tiến | `src/student_agent/trace.py`, `src/student_agent/submission.py`, `tests/test_workflow.py`, `tests/test_release_safety.py` |
| Contract/schema, validation và đóng gói artifact V2 | Luân, Tiến | `src/student_agent/contracts.py`, `src/student_agent/submission.py`, `tests/test_starter.py` |
| Error analysis, edge cases và chiến lược cải thiện | Duy, Tiến | `STRATEGY_RANK2_ANALYSIS.md`, `SUBMISSION_091648_AUDIT.md`, `ARCHITECTURE.md` |
| Kiểm thử hồi quy canceled order paid và split payment | Duy, Luân, Tiến | `tests/test_workflow.py`, commit `1b82b5c` |

## Ghi chú về lịch sử Git

Remote `origin/main` hiện ghi nhận các commit triển khai của **Thái Phúc Tiến** (`1cf6913`, `3937322`, `1b82b5c`). Các commit trước đó mang tác giả `khvavuong` là phần starter/configuration ban đầu của repository. Công việc của Long, Luân và Duy được thực hiện theo hình thức phối hợp, review và kiểm thử trên cùng máy/nhánh rồi được trưởng nhóm tích hợp; vì vậy bảng trên dùng file, test và kết quả audit để mô tả phần việc, không gán sai commit author.

Trước khi nộp, từng thành viên cần xác nhận nội dung phần việc của mình. Nếu giảng viên yêu cầu bằng chứng Git cá nhân, mỗi thành viên nên tạo commit hoặc pull request bằng đúng tài khoản của mình cho phần thay đổi tiếp theo.
