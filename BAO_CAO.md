# Báo cáo hoàn thành LAB12 — HITL PR Review Agent

## Phạm vi

Đã đọc toàn bộ README, bốn bài trong `exercises`, giao diện `app.py`, các module
`common`, audit schema và công cụ replay. Đã hoàn thành cả 5 bài bắt buộc.
SQLite được dùng theo hướng dẫn của scaffold, không yêu cầu cài PostgreSQL.
Các module được cung cấp trong `common` giữ nguyên.

| Bài | Phần đã hoàn thành |
| --- | --- |
| 1 | Lấy PR, phân tích có cấu trúc `PRAnalysis`, nối graph và định tuyến đủ 3 nhánh. |
| 2 | `interrupt()`, `MemorySaver`, Approve/Reject/Edit và vòng lặp `Command(resume=...)`. |
| 3 | Câu hỏi gắn với diff, thu thập câu trả lời, gọi LLM lại với phân tích gốc và Q&A. |
| 4 | `AsyncSqliteSaver`, log sự kiện bằng `AuditEntry`, khôi phục phiên và replay. |
| 5 | Streamlit: nhập PR, duyệt, sửa review, trả lời câu hỏi, xác nhận review mới và mở lại phiên cũ. |

## Chạy trên Windows / PowerShell

Mở terminal tại thư mục `VinUni-AI20k-Day27-Track03-HITL`.
Toàn bộ thư viện đã được cài trong môi trường ảo **`env`** của dự án.
Dùng trực tiếp Python của môi trường này để tránh vướng ExecutionPolicy khi activate:

```powershell
.\env\Scripts\python.exe -X utf8 -m streamlit run app.py
```

Mở `http://localhost:8501`. Nhập một trong hai URL:

```text
https://github.com/VinUni-AI20k/PR-Demo/pull/1
https://github.com/VinUni-AI20k/PR-Demo/pull/2
```

Nếu cần cài lại đúng các phiên bản trong lockfile vào `env`:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "env"
uv sync --locked
```

Nếu muốn activate môi trường trước, dùng ` .\env\Scripts\Activate.ps1 `.
Tham số `-X utf8` trong các lệnh Python giúp hiển thị tiếng Việt và ký tự Rich
trên terminal Windows có encoding mặc định khác UTF-8.

## Cấu hình

`.env` chứa cấu hình kết nối riêng, đã nằm trong `.gitignore` cùng `env` và cache.
Không đưa `.env` vào bài nộp công khai. `.env.example` chỉ chứa mẫu cấu hình.

Dự án hiện dùng OpenAI trực tiếp: `LLM_MODEL=gpt-4o-mini` và
`LLM_BASE_URL=https://api.openai.com/v1`. Biến chứa API key vẫn mang tên
`OPENROUTER_API_KEY` vì đây là giao diện sẵn có của `common/llm.py`.
`GITHUB_USER` dùng để ghi username người duyệt vào audit; đây là thông tin cấu hình,
không phải một cơ chế xác thực nhiều người dùng.

Các khóa được chia sẻ trong chat nên được thu hồi/thay mới sau khi sử dụng và cập nhật
lại ở máy cá nhân. Báo cáo và mã nguồn không chứa giá trị khóa.

## Quy tắc xử lý

Theo hằng số được cung cấp trong `common/schemas.py`:

| Confidence | Xử lý |
| --- | --- |
| `>= 0.73` | `auto_approve`, tự đăng review. |
| `>= 0.58` và `< 0.73` | `human_approval`, chờ Approve/Reject/Edit. |
| `< 0.58` | `escalate`, hỏi reviewer trước khi phân tích lại. |

Do đó `0.58` thuộc nhánh duyệt thủ công. Mã dùng chính các hằng số của scaffold,
không thay ngưỡng để ép kết quả. Prompt yêu cầu nêu rủi ro và chuyển cấp khi còn
vấn đề bảo mật chưa được làm rõ.

**Edit** nhận toàn bộ nội dung review thay thế trong ô nhập. Nội dung này được
đăng nguyên văn sau khi reviewer chọn Edit; nội dung trống bị chặn.

Trong bài 3–4 chạy terminal, trả lời Q&A xong sẽ đăng review đã tổng hợp theo yêu cầu
hai bài này. Giao diện bài 5 thêm bước duyệt cuối sau Q&A: reviewer có thể
Approve, Reject hoặc Edit bản mới trước khi đăng, kể cả khi confidence mới đã cao.

Các sự kiện chờ duyệt được ghi ở node riêng trước node chứa `interrupt()`.
Cách này tránh ghi lặp sự kiện chờ khi node interrupt được chạy lại lúc resume,
đúng với [cơ chế interrupt của LangGraph](https://docs.langchain.com/oss/python/langgraph/interrupts).
Tác dụng phụ đăng GitHub được thực hiện ở node sau interrupt.

## Lệnh cho từng bài

```powershell
.\env\Scripts\python.exe -X utf8 exercises/exercise_1_confidence.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
.\env\Scripts\python.exe -X utf8 exercises/exercise_2_hitl.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
.\env\Scripts\python.exe -X utf8 exercises/exercise_3_escalation.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/2
.\env\Scripts\python.exe -X utf8 exercises/exercise_4_audit.py --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
.\env\Scripts\python.exe -X utf8 -m audit.replay --list
.\env\Scripts\python.exe -X utf8 -m audit.replay --thread <thread_id>
.\env\Scripts\python.exe -X utf8 exercises/exercise_4_audit.py --thread <thread_id>
```

`thread_id` được in ra terminal; trên web nó được lưu trong session state và URL.
Reload cùng URL hoặc chọn **Saved session → Open session** để mở lại checkpoint.
Không dùng một thread cho hai PR khác nhau. Mở phiên đã hoàn tất chỉ đọc kết quả,
không khởi động lại graph và không tự đăng lại bình luận.

## Kiểm chứng

Chạy toàn bộ kiểm thử không cần mạng và không tốn API:

```powershell
.\env\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

Kết quả kiểm tra cuối: **16 kiểm thử đạt**, không có kiểm thử bị bỏ qua.

Các test dùng LangGraph, SQLite và giao diện Streamlit thật qua
[Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).
Chỉ mock các biên gọi LLM/GitHub. Phạm vi kiểm tra gồm:

- Tất cả ngưỡng phân luồng, bao gồm `0.58`, `0.72` và `0.73`.
- Graph tạm dừng trước khi đăng; Approve/Edit đăng đúng một lần; Reject không đăng.
- Q&A được đưa vào prompt tổng hợp cùng diff và phân tích gốc.
- Đóng và mở lại kết nối SQLite rồi resume được phiên cũ.
- Audit có đủ sự kiện chờ/đã trả lời, reviewer, confidence mới và thời gian thực thi.
- Bảng phiên gần đây xếp mức rủi ro theo `high > med > low`.
- Reload web giữ được phiên; form kiểm tra URL, nội dung Edit và câu trả lời bắt buộc.
- Reject sau Q&A vẫn không đăng; lỗi đăng tự động không hiển thị thông báo thành công.

Kiểm tra kết nối thật (chỉ đọc GitHub, gọi OpenAI, không đăng bình luận):

```powershell
.\env\Scripts\python.exe -X utf8 -m scripts.verify_connections
```

Đã kiểm tra thành công ngày **15/10/2026** với tài khoản GitHub cấu hình.
Lần kiểm tra cuối: PR #1 confidence **0.58 → human_approval**;
PR #2 confidence **0.55 → escalate**. Kết quả chi tiết, rủi ro và câu hỏi nằm ở
[`artifacts/connection_check.json`](artifacts/connection_check.json).
LLM có thể trả confidence khác giữa các lần gọi; đây là số tự báo cáo, chưa được hiệu chỉnh.
OpenAI structured output được dùng qua schema Pydantic như
[hướng dẫn Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat).

## Giới hạn kiểm chứng

Chưa đăng bình luận thật lên GitHub. Thành công khi đọc PR không chứng minh token
có quyền ghi; nếu đăng bị 403, cần kiểm tra phạm vi repository và quyền ghi tương ứng.

Việc resume thông thường đã được kiểm tra không tạo bình luận lặp. Tuy nhiên,
nếu tiến trình bị ngắt ngay sau khi GitHub nhận bình luận nhưng trước khi checkpoint
được ghi, scaffold chưa có cơ chế idempotency xuyên hai hệ thống để bảo đảm exactly-once.

Giao diện đã được kiểm tra bằng AppTest; môi trường không có Chrome DevTools MCP
để kiểm tra trực quan và accessibility bằng trình duyệt thật.
