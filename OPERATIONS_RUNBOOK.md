# Operations Runbook

## 1. Mục tiêu

Runbook này mô tả cách vận hành và xử lý sự cố thường gặp với `policyd-py` trong production.

## 2. Thành phần chính cần nhớ

- `policyd-py`: service chính nhận request policy từ Postfix.
- Redis/KeyDB: lưu rate-limit, lock, penalty, notify cooldown, list membership.
- Postfix: gọi `check_policy_service`.
- External action:
  - script
  - webhook
- Management API: tùy chọn, phục vụ health/stats/manual action.

## 3. Lệnh kiểm tra cơ bản

### Kiểm tra process

```bash
systemctl status policyd-py
journalctl -u policyd-py -n 200 --no-pager
```

### Kiểm tra socket

```bash
ls -l /var/run/gopolicyd.sock
ss -xl | grep gopolicyd
```

### Kiểm tra Redis

```bash
redis-cli -n 0 ping
redis-cli -n 0 config get notify-keyspace-events
redis-cli -n 0 info stats
```

### Kiểm tra management API

```bash
curl http://127.0.0.1:8080/health
curl -u admin:secret http://127.0.0.1:8080/api/v1/stats
```

## 4. Checklist chẩn đoán nhanh

Khi có sự cố, kiểm tra theo thứ tự:

1. Service còn sống không.
2. Redis còn sống không.
3. Socket policy còn tồn tại không.
4. Postfix có đang gọi đúng socket không.
5. Log có lỗi Redis, timeout, queue đầy, lock/unlock lỗi không.
6. Nếu bật management API, kiểm tra `/health` và `/api/v1/stats`.

## 5. Tình huống sự cố thường gặp

### A. Postfix gửi mail chậm hoặc treo ở policy check

Triệu chứng:
- SMTP transaction chậm.
- Queue Postfix tăng.
- `journalctl` thấy timeout hoặc lỗi Redis.

Kiểm tra:

```bash
systemctl status policyd-py
redis-cli ping
journalctl -u policyd-py -n 200 --no-pager
postconf | grep check_policy_service
```

Nguyên nhân thường gặp:
- Redis chậm hoặc down.
- DNS validation bật và DNS resolver chậm.
- External dependency làm hệ thống bão hòa.
- `worker_count` hoặc tài nguyên host không đủ.

Hành động:
- Xác nhận Redis trước.
- Nếu khẩn cấp, tạm bỏ `check_policy_service` khỏi Postfix và reload Postfix.
- Sau đó điều tra sâu hơn.

### B. Redis down hoặc latency quá cao

Triệu chứng:
- Policy trả lỗi hoặc phản hồi chậm.
- Log có `Rate limit check error`.
- `/api/v1/stats` có `total_errors` tăng.

Kiểm tra:

```bash
redis-cli ping
redis-cli info memory
redis-cli info stats
```

Hành động:
- Khôi phục Redis.
- Kiểm tra network giữa app và Redis.
- Kiểm tra memory pressure / eviction.
- Nếu không xử lý nhanh được, cân nhắc tạm bỏ policy check khỏi Postfix.

### C. User bị lock nhầm hoặc không unlock

Triệu chứng:
- Người dùng báo account bị khóa lâu.
- Lock tồn tại nhưng không mở.

Kiểm tra:

```bash
curl -u admin:secret http://127.0.0.1:8080/api/v1/runtime/state/user@example.com
redis-cli get lock:user@example.com
redis-cli ttl lock:user@example.com
redis-cli get lockmeta:user@example.com
```

Nếu cần thao tác tay:

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/users/unlock \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com"}'

curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/penalty/user@example.com/reset
```

Nếu dùng script provider, kiểm tra thêm:

```bash
tail -n 100 /tmp/policyd_script_actions.log
cat /tmp/policyd_locked_accounts.db
```

### D. Notify không gửi

Triệu chứng:
- Account lock xảy ra nhưng không có cảnh báo.

Kiểm tra:

```bash
journalctl -u policyd-py -n 200 --no-pager | grep -i notify
tail -n 100 /tmp/policyd_script_notifications.log
```

Nguyên nhân:
- Queue notify đầy.
- Script notify lỗi.
- Telegram/Discord/SMTP config sai.

Hành động:
- Kiểm tra script/endpoint notify.
- Kiểm tra log warning liên quan notify.
- Nếu notify không critical, ghi nhận và xử lý sau khi service ổn định.

### E. Management API không truy cập được

Kiểm tra:

```bash
ss -ltnp | grep 8080
journalctl -u policyd-py -n 200 --no-pager
curl http://127.0.0.1:8080/health
```

Nguyên nhân:
- `[Web].enable=false`
- bind sai host/port
- thiếu `aiohttp`
- auth sai

Hành động:
- Xác nhận config `[Web]`
- Kiểm tra dependency
- Kiểm tra firewall/reverse proxy

## 6. Lệnh vận hành chuẩn

### Reload config

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/config/reload
```

### Lock thủ công

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/users/lock \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","reason":"manual"}'
```

### Unlock thủ công

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/users/unlock \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com"}'
```

### Reset rate-limit

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/ratelimit/user@example.com/reset
```

### Reset penalty

```bash
curl -u admin:secret -X POST http://127.0.0.1:8080/api/v1/penalty/user@example.com/reset
```

## 7. Khi cần rollback nhanh

Mục tiêu: khôi phục khả năng gửi mail trước, rồi mới điều tra.

Phương án 1:
- Tạm bỏ `check_policy_service` khỏi Postfix.
- Reload Postfix.

Phương án 2:
- Rollback config cũ của `policyd-py`.
- Restart service.

Phương án 3:
- Nếu vấn đề nằm ở external action, tạm chuyển `provider` sang chế độ an toàn hoặc disable external action.

## 8. Điều tra sau sự cố

Sau khi ổn định:
- Thu thập log service, Redis, Postfix.
- Xác định thời điểm bắt đầu lỗi.
- Xác định lỗi nằm ở request path hay external action path.
- Kiểm tra số lock/unlock/notify lỗi trong khoảng thời gian sự cố.
- Cập nhật lại SOP nếu cần.

## 9. Dữ liệu và file quan trọng

- Config chính:
  - `/etc/policyd/config.ini`
- Script mẫu:
  - `scripts/account_action.sh`
  - `scripts/notify_action.sh`
- File state mẫu khi dùng script:
  - `/tmp/policyd_locked_accounts.db`
- File log mẫu khi dùng script:
  - `/tmp/policyd_script_actions.log`
  - `/tmp/policyd_script_notifications.log`

## 10. Khi nào cần escalation

Escalate ngay nếu:
- Redis mất ổn định kéo dài.
- SMTP transaction bị chậm diện rộng.
- User bị lock nhầm trên diện rộng.
- Unlock không chạy sau khi TTL hết hàng loạt.
- Queue lỗi tăng liên tục sau restart.
