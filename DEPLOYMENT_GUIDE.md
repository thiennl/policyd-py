# Deployment Guide

## 1. Mục tiêu

Tài liệu này mô tả cách triển khai `policyd-py` cho mô hình `single-node` production với Postfix và Redis/KeyDB, dùng external scripts hoặc webhook cho lock/unlock account.

## 2. File nên dùng

- File cấu hình đầy đủ, dạng chờ để điền dần: [deploy/config.full-commented.ini](/opt/repos/policyd-py/deploy/config.full-commented.ini)
- File cấu hình production tối ưu sẵn để copy chỉnh: [deploy/config.production.ini](/opt/repos/policyd-py/deploy/config.production.ini)
- File mẫu gốc của repo: [config.ini.example](/opt/repos/policyd-py/config.ini.example)

## 3. Chuẩn bị hệ thống

Yêu cầu:
- Linux
- Python 3.11 hoặc 3.12
- Redis hoặc KeyDB
- Postfix
- quyền tạo Unix socket tại `/var/run/gopolicyd.sock`

Tạo thư mục triển khai:

```bash
mkdir -p /opt/project/policyd
mkdir -p /etc/policyd
mkdir -p /opt/policyd/scripts
```

## 4. Cài application

```bash
cd /opt/project/policyd
git clone <repo-url> policyd-py
cd policyd-py
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Không dùng `venv/` đã nằm sẵn trong source snapshot. Tạo virtualenv mới ở máy deploy.

## 5. Tạo file config active

Khuyến nghị khởi đầu bằng file production:

```bash
cp /opt/project/policyd/policyd-py/deploy/config.production.ini /etc/policyd/config.ini
```

Nếu muốn rà kỹ từng section trước khi bật production:

```bash
cp /opt/project/policyd/policyd-py/deploy/config.full-commented.ini /etc/policyd/config.ini
```

Những giá trị phải thay ngay:
- `[KeyDB].password`
- `[DomainLists].local_domains`
- `[Script].lock_command`
- `[Script].unlock_command`
- `[Script].status_command`
- `[Web].password` hoặc `[Web].bearer_token`
- các path blacklist trong `[EmailValidation]`

## 6. Cấu hình production khuyến nghị

Để ưu tiên throughput và độ ổn định:
- giữ `ratelimit_use_lua = true`
- giữ `validate_recipient_deliverability = false`
- giữ `policy_check_state = RCPT`
- dùng `worker_count = 300` làm baseline, benchmark rồi mới tăng
- dùng `provider = script` nếu lock/unlock qua local command
- bật `async_execution = true` cho external action nếu script/webhook có thể chậm
- chỉ bind management API vào `127.0.0.1`

## 7. Cấu hình external script

Ví dụ file `/opt/policyd/scripts/account_action.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
email="${2:-}"
reason="${3:-}"

case "$action" in
  lock)
    /opt/zimbra/bin/zmprov ma "$email" zimbraAccountStatus locked
    ;;
  unlock)
    /opt/zimbra/bin/zmprov ma "$email" zimbraAccountStatus active
    ;;
  status)
    status="$(/opt/zimbra/bin/zmprov ga "$email" zimbraAccountStatus 2>/dev/null | awk '/zimbraAccountStatus:/ {print $2}')"
    printf '%s\n' "${status:-active}"
    ;;
  *)
    echo "unknown action: $action" >&2
    exit 1
    ;;
esac
```

Set quyền:

```bash
chmod 755 /opt/policyd/scripts/account_action.sh
```

Yêu cầu:
- script phải trả exit code `0` khi thành công
- `status` nên in `locked` hoặc `active`
- script phải idempotent, chịu được gọi lặp
- user chạy service phải có quyền thực thi command thật

## 8. Cấu hình Postfix

Trong `main.cf`:

```ini
smtpd_recipient_restrictions =
    permit_mynetworks,
    permit_sasl_authenticated,
    reject_unauth_destination,
    check_policy_service unix:/var/run/gopolicyd.sock
```

Socket trong Postfix phải khớp `[General].socket`.

## 9. Chạy foreground để smoke test

```bash
cd /opt/project/policyd/policyd-py
. .venv/bin/activate
POLICYD_CONFIG=/etc/policyd/config.ini python -m policyd_py
```

Kiểm tra:
- process không văng exception
- kết nối Redis thành công
- socket `/var/run/gopolicyd.sock` được tạo
- script lock/unlock chạy được nếu gọi tay

## 10. Tạo service systemd

Ví dụ `/etc/systemd/system/policyd-py.service`:

```ini
[Unit]
Description=policyd-py SMTP policy daemon
After=network.target
After=redis.service

[Service]
Type=simple
User=postfix
Group=postfix
WorkingDirectory=/opt/project/policyd/policyd-py
Environment=POLICYD_CONFIG=/etc/policyd/config.ini
ExecStart=/opt/project/policyd/policyd-py/.venv/bin/python -m policyd_py
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Apply:

```bash
systemctl daemon-reload
systemctl enable --now policyd-py
systemctl status policyd-py
journalctl -u policyd-py -n 200 --no-pager
```

## 11. Bật management API an toàn

Khuyến nghị:
- `host = 127.0.0.1`
- bật `password` hoặc `bearer_token`
- không expose trực tiếp ra Internet

Kiểm tra:

```bash
curl http://127.0.0.1:8080/health
curl -u admin:CHANGE_ME http://127.0.0.1:8080/api/v1/stats
```

## 12. Kiểm thử trước go-live

Checklist tối thiểu:
- Redis ping ổn định
- Postfix reload xong không lỗi config
- request hợp lệ trả `DUNNO`
- request vượt quota trả `DEFER`
- lock script chạy đúng
- unlock script chạy đúng
- notify script ghi log đúng
- `/api/v1/stats` trả dữ liệu

## 13. Tuning cho production

Nếu mục tiêu tải cao:
- tăng `worker_count` dần: 300 -> 400 -> 600, benchmark từng bước
- giữ Redis local/private network, RTT thấp
- không bật `validate_recipient_deliverability` nếu chưa có benchmark
- giảm số lượng rule/list lookup trong hot path
- nếu tỷ lệ exceed cao, giữ `ExternalAction.async_execution = true`
- theo dõi queue và latency của external action riêng

## 14. Các lỗi cấu hình thường gặp

- `provider = script` nhưng `lock_command` hoặc `unlock_command` để trống
- path script đúng nhưng chưa có executable bit
- `[Web].enable = true` nhưng chưa cài `aiohttp`
- dùng `from_addr` thay vì `from` trong section `[Email]`
- dùng `file://` cho list nhưng file không tồn tại tại máy deploy
- dùng `venv` commit sẵn thay vì virtualenv mới

## 15. File tham chiếu

- config parser: [policyd_py/config/settings.py](/opt/repos/policyd-py/policyd_py/config/settings.py)
- runtime bootstrap: [policyd_py/__main__.py](/opt/repos/policyd-py/policyd_py/__main__.py)
- policy handler: [policyd_py/policy/handler.py](/opt/repos/policyd-py/policyd_py/policy/handler.py)
- rate limiter: [policyd_py/ratelimit/limiter.py](/opt/repos/policyd-py/policyd_py/ratelimit/limiter.py)
- management API: [policyd_py/management/api_server.py](/opt/repos/policyd-py/policyd_py/management/api_server.py)
