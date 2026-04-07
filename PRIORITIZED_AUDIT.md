# Prioritized Audit

## Mục tiêu

Tài liệu này tổng hợp vòng audit tiếp theo với trọng tâm là các hạng mục cần ưu tiên sửa trước để tăng độ an toàn và khả năng vận hành production cho `policyd-py`.

## Ưu tiên Fix

### 1. Khóa management API theo kiểu fail-closed

Hiện tại nếu bật `[Web].enable=true` nhưng quên cấu hình auth thì API admin có thể mở hoàn toàn.

Cần sửa để process không khởi động nếu web được bật mà không có:
- `bearer_token`
- hoặc cặp `username/password` hợp lệ

Điểm sửa chính:
- `policyd_py/management/api_server.py`
- `policyd_py/__main__.py`

### 2. Loại hoặc sửa triệt để DNS deliverability check khỏi hot path

Implementation hiện tại:
- không dùng `dns_timeout`
- không kiểm tra MX thật
- có thể kéo dài latency SMTP theo resolver của hệ điều hành

Nếu cần production an toàn:
- mặc định nên tắt
- nếu giữ thì phải dùng timeout thật
- và kiểm tra đúng semantics mail routing

Điểm sửa chính:
- `policyd_py/validation/validator.py`
- `policyd_py/config/settings.py`

### 3. Giới hạn hàng đợi background trong Redis

`drop_if_full` hiện chỉ tồn tại ở interface nhưng implementation không dùng. Queue Redis dạng list có thể phình vô hạn khi lock/notify bị nghẽn.

Cần thêm cơ chế:
- đo độ dài queue
- áp policy rõ ràng
- `notify`: có thể drop
- `lock/unlock`: không drop nhưng phải có guardrail và metric

Điểm sửa chính:
- `policyd_py/policy/handler.py`

### 4. Sửa semantics hot reload

Reload config hiện không áp dụng đầy đủ cho concurrency model.

Ví dụ:
- `worker_count` thay đổi nhưng semaphore cũ vẫn giữ nguyên
- background workers không scale lại theo config mới

Có 2 hướng:
- đơn giản: ghi rõ `worker_count` cần restart
- đầy đủ: rebuild server concurrency và worker pool khi reload

Điểm sửa chính:
- `policyd_py/core/server.py`
- `policyd_py/policy/handler.py`
- `policyd_py/__main__.py`

### 5. Làm sạch config và implementation mismatch

Một số field đang được parse nhưng không có hiệu lực runtime hoặc gần như không được dùng:
- `cluster_mode`
- `write_timeout`
- `unlock_ttl_threshold`
- `dns_timeout`
- `allow_smtputf8`
- `allow_quoted_local`
- `allow_domain_literal`
- `logging.level`

Cần chọn một trong hai hướng:
- implement thật
- hoặc bỏ khỏi config/docs

Điểm sửa chính:
- `policyd_py/config/settings.py`

## Nhóm fix tiếp theo

- Bảo vệ thao tác xóa socket cũ an toàn hơn trong `policyd_py/core/server.py`
- Làm rõ contract của API lock thủ công: nếu có `duration` thì phải hỗ trợ thật, nếu không thì bỏ khỏi API
- Bổ sung metrics cho queue backlog, notify drop, action latency, Redis errors
- Bổ sung test cho auth fail-closed, queue pressure, hot reload semantics, DNS timeout behavior

## Thứ tự triển khai thực tế

### Sprint 1

- fix `management API`
- fix `DNS validation`
- fix `queue bounding`

### Sprint 2

- fix `hot reload`
- fix `config mismatch cleanup`

### Sprint 3

- hardening vận hành
- bổ sung test
- hoàn thiện CI/verifications

## Kết luận

Nếu chỉ được sửa ít mà cần tăng độ an toàn production nhanh nhất, nên làm theo đúng thứ tự:

1. `API auth`
2. `DNS hot path`
3. `queue control`

Ba mục này giảm rủi ro bảo mật và treo hệ thống rõ rệt nhất.
