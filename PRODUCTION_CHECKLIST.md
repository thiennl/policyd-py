# Production Checklist

## 1. Mục tiêu

Checklist này dùng để xác nhận `policyd-py` sẵn sàng chạy production trong vai trò SMTP policy daemon cho Postfix trên mô hình `single-node` với Redis/KeyDB backend.

## 2. Hạ tầng hệ điều hành

- [ ] Tạo user service riêng, không chạy process bằng `root`.
- [ ] Đảm bảo thư mục chứa Unix socket tồn tại trước khi service khởi động.
- [ ] Đảm bảo Postfix và `policyd-py` cùng truy cập được socket policy.
- [ ] Cấu hình `systemd` với `Restart=always`.
- [ ] Đặt `LimitNOFILE` đủ cao cho số connection dự kiến.
- [ ] Bật đồng bộ thời gian hệ thống bằng `chrony` hoặc tương đương.
- [ ] Bật log rotation nếu ghi log ra file.

## 3. Python runtime và package

- [ ] Dùng virtualenv thực tế được tạo mới từ môi trường deploy.
- [ ] Cài dependency từ `requirements.txt`.
- [ ] Kiểm tra `python3 -V` khớp version đã chuẩn hóa cho production.
- [ ] Không dùng `venv` đã commit cùng source snapshot.
- [ ] Chạy smoke test import:

```bash
python3 -m py_compile policyd_py/__main__.py
```

## 4. Redis / KeyDB

- [ ] Redis/KeyDB chạy ổn định, độ trễ thấp, gần application.
- [ ] Kiểm tra `notify-keyspace-events` có bật `Ex`.
- [ ] Đảm bảo Redis không bật eviction policy gây mất key runtime ngẫu nhiên.
- [ ] Theo dõi memory usage, ops/sec, latency, connected clients.
- [ ] Xác nhận topology backend thực tế là `single Redis` nếu chưa triển khai cluster/sentinel thật.
- [ ] Có alert khi Redis không ping được hoặc latency tăng kéo dài.

## 5. Cấu hình chương trình

- [ ] Tạo file config thật ở `/etc/policyd/config.ini` hoặc đường dẫn chuẩn của hệ thống.
- [ ] Export `POLICYD_CONFIG` đúng path config nếu không dùng mặc định.
- [ ] Rà lại `[General]`:
  - [ ] `socket`
  - [ ] `socket_permission`
  - [ ] `worker_count`
  - [ ] timeout đọc/ghi
- [ ] Rà lại `[KeyDB]`:
  - [ ] host/port
  - [ ] password
  - [ ] db
- [ ] Rà lại `[Locks]`, `[Penalty]`, `[Limits]`, `[Quotas]`, `[Policies]` khớp nghiệp vụ mail thật.
- [ ] Nếu bật `[AdaptiveLimits]`, xác minh trusted list sạch và không nới quota nhầm đối tượng.
- [ ] Nếu bật `[EmailValidation]`, chỉ bật các check thực sự cần cho production.
- [ ] Nếu dùng script provider, xác minh `[Script]` trỏ đúng đường dẫn script.

## 6. External action / notify

- [ ] Nếu dùng `provider = script`, kiểm tra executable bit của script:

```bash
ls -l scripts/account_action.sh scripts/notify_action.sh
```

- [ ] Nếu dùng script riêng ngoài repo, kiểm tra:
  - [ ] file tồn tại
  - [ ] executable
  - [ ] timeout phù hợp
  - [ ] exit code rõ ràng
- [ ] Nếu dùng webhook, kiểm tra endpoint và credential trước khi bật production.
- [ ] Nếu notify là best-effort, xác nhận đội vận hành chấp nhận event notify có thể bị drop khi queue đầy.

## 7. Postfix integration

- [ ] Cấu hình `main.cf` có `check_policy_service` đúng socket.
- [ ] Reload Postfix sau khi cập nhật config.
- [ ] Test bằng SMTP flow thật hoặc mô phỏng:
  - [ ] mail authenticated hợp lệ
  - [ ] mail external hợp lệ
  - [ ] mail vượt quota
  - [ ] mail từ sender/domain blacklist

## 8. Management API

- [ ] Chỉ bật `[Web].enable = true` nếu thực sự cần.
- [ ] Nếu bật, cấu hình auth:
  - [ ] Basic auth hoặc
  - [ ] Bearer token
- [ ] Bind API vào `127.0.0.1` nếu chỉ dùng nội bộ.
- [ ] Nếu cần expose ngoài host, cấu hình firewall/reverse proxy bảo vệ thêm.
- [ ] Kiểm tra các endpoint chính:

```bash
curl http://127.0.0.1:8080/health
curl -u admin:secret http://127.0.0.1:8080/api/v1/stats
```

## 9. Smoke test trước go-live

- [ ] Khởi động service foreground hoặc systemd.
- [ ] Kiểm tra log khởi động không có exception.
- [ ] Kiểm tra Redis connection thành công.
- [ ] Kiểm tra policy socket được tạo đúng path.
- [ ] Kiểm tra `/health` nếu bật management API.
- [ ] Kiểm tra một request SMTP hợp lệ đi qua được.
- [ ] Kiểm tra một request vượt quota trả về `DEFER`.
- [ ] Nếu dùng external action, xác nhận lock/unlock thực sự chạy.
- [ ] Nếu dùng notify script, xác nhận log notify được ghi.

## 10. Observability

- [ ] Thu thập log service tập trung.
- [ ] Theo dõi tối thiểu:
  - [ ] `total_requests`
  - [ ] `total_rate_limited`
  - [ ] `total_errors`
  - [ ] `active_connections`
  - [ ] `total_users_locked`
  - [ ] action metrics nếu bật external action
- [ ] Đặt alert khi:
  - [ ] error tăng đột biến
  - [ ] Redis timeout
  - [ ] lock/unlock lỗi liên tục
  - [ ] queue đầy thường xuyên

## 11. Benchmark và kiểm thử tải

- [ ] Chạy benchmark baseline trước production.
- [ ] Đo p50/p95/p99 latency.
- [ ] Đo throughput tối đa trước khi Redis hoặc CPU bão hòa.
- [ ] Test burst traffic với tỷ lệ rate-limit cao.
- [ ] Test trường hợp downstream script/webhook chậm.

## 12. Deploy và rollback

- [ ] Backup config hiện tại trước khi thay đổi.
- [ ] Mọi thay đổi config đều có người kiểm tra chéo.
- [ ] Sau deploy, chạy lại smoke test mục 9.
- [ ] Có rollback plan về config cũ và binary/source cũ.
- [ ] Nếu cần disable nhanh, có SOP tạm bỏ `check_policy_service` khỏi Postfix.

## 13. Chốt go-live

- [ ] Tài liệu cấu hình production đã lưu.
- [ ] Runbook sự cố đã chia sẻ cho đội trực vận hành.
- [ ] Danh sách contact on-call đã sẵn sàng.
- [ ] Kế hoạch quan sát 24h đầu sau go-live đã được phân công.
