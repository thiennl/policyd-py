# policyd-py

`policyd-py` là SMTP policy daemon viết bằng Python, dùng cho Postfix để kiểm soát rate limit, blacklist, validation và account lock/unlock tự động.

Trạng thái hiện tại phù hợp nhất cho mô hình `single-node` production. Repo đã có:
- `fixed window`
- `token bucket`
- `sliding window counter`
- `progressive penalty`
- `adaptive limits`
- LDAP-backed list loader
- Webhook external action
- Script-based external action/notification
- management API local
- hot reload config

## 1. Yêu cầu hệ thống

- Linux
- Python `3.11+` hoặc `3.12`
- Redis hoặc KeyDB
- Postfix policy delegation
- Tùy chọn:
  - LDAP nếu muốn load domain/list từ LDAP
  - `aiohttp` cho management API

## 2. Cài đặt

### 2.1. Clone repo

```bash
cd /opt/project/policyd
git clone <repo-url> policyd-py
cd policyd-py
```

### 2.2. Tạo virtualenv và cài dependency

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

### 2.3. Tạo file cấu hình

```bash
mkdir -p /etc/policyd
cp config.ini.example /etc/policyd/config.ini
```

Biến môi trường dùng để chỉ định config:

```bash
export POLICYD_CONFIG=/etc/policyd/config.ini
```

## 3. Chạy service

### 3.1. Chạy foreground

```bash
. venv/bin/activate
POLICYD_CONFIG=/etc/policyd/config.ini python -m policyd_py
```

### 3.2. Các thành phần runtime chính

Khi process khởi động, service sẽ:
- đọc config từ `POLICYD_CONFIG` hoặc mặc định `/etc/policyd/config.ini`
- kết nối Redis/KeyDB
- bật Redis keyspace notifications cho unlock event
- load Lua scripts cho rate limit nếu `ratelimit_use_lua=true`
- load list/domain vào Redis
- khởi tạo policy socket server
- khởi tạo management API nếu `[Web].enable=true`

## 4. Tích hợp với Postfix

Ví dụ `main.cf`:

```ini
smtpd_recipient_restrictions =
    permit_mynetworks,
    permit_sasl_authenticated,
    reject_unauth_destination,
    check_policy_service unix:/var/run/gopolicyd.sock
```

Repo mặc định dùng Unix socket tại:

```ini
[General]
socket = /var/run/gopolicyd.sock
socket_permission = 0666
```

Đảm bảo Postfix và process `policyd-py` cùng truy cập được socket này.

## 5. Cấu hình chính

Cấu hình dùng định dạng INI. File mẫu đầy đủ: [config.ini.example](/opt/project/policyd/policyd-py/config.ini.example)

### 5.1. `[General]`

Điều khiển socket và worker:

```ini
[General]
socket = /var/run/gopolicyd.sock
socket_permission = 0666
debug = false
worker_count = 200
client_read_timeout = 30
client_write_timeout = 30
```

### 5.2. `[KeyDB]`

Redis/KeyDB backend:

```ini
[KeyDB]
hosts = 127.0.0.1:6379
password =
db = 0
cluster_mode = false
connect_timeout = 5
read_timeout = 3
write_timeout = 3
```

### 5.3. `[Locks]`

Thời gian khóa account:

```ini
[Locks]
lock_duration = 600
unlock_ttl_threshold = 11
```

`lock_duration` là mặc định cho lock cơ bản. Nếu bật `Penalty`, duration thực tế có thể bị nâng lên theo từng mức.

### 5.4. `[Penalty]`

Progressive penalty:

```ini
[Penalty]
enable = true
ttl = 1d
steps = 10m,30m,2h
```

Ý nghĩa:
- lần vi phạm đầu: lock `10m`
- tiếp theo trong cùng `ttl`: lock `30m`
- tiếp nữa: lock `2h`

Reset penalty bằng API riêng hoặc chờ hết TTL.

### 5.5. `[Limits]`

Bật/tắt policyd và quota mặc định:

```ini
[Limits]
enable_policyd = true
policy_check_state = RCPT
ratelimit_use_lua = true
default_quota = 100/1h:fixed_window,30/5m:sliding_window_counter
```

`policy_check_state` thường để `RCPT`.

### 5.6a. `[Script]`

Cho phép thay `lock/unlock/status` và `notify` bằng external script:

```ini
[ExternalAction]
enable = true
provider = script

[Script]
lock_command = /opt/repos/policyd-py/scripts/account_action.sh lock ${email} ${reason}
unlock_command = /opt/repos/policyd-py/scripts/account_action.sh unlock ${email}
status_command = /opt/repos/policyd-py/scripts/account_action.sh status ${email}
notify_command = /opt/repos/policyd-py/scripts/notify_action.sh ${event} ${email} ${message}
timeout_seconds = 10
```

Biến template có thể dùng:
- `${action}`
- `${email}`
- `${reason}`
- `${event}`
- `${message}`
- `${duration_seconds}`
- `${timestamp}`

Hai script mẫu đã có sẵn trong repo:
- `scripts/account_action.sh`: mô phỏng lock/unlock/status bằng file state local.
- `scripts/notify_action.sh`: ghi event notify ra log file local.

Mặc định:
- account state lưu ở `POLICYD_SCRIPT_LOCK_DB` hoặc `/tmp/policyd_locked_accounts.db`
- action log lưu ở `POLICYD_SCRIPT_LOG_FILE` hoặc `/tmp/policyd_script_actions.log`
- notify log lưu ở `POLICYD_SCRIPT_NOTIFY_LOG_FILE` hoặc `/tmp/policyd_script_notifications.log`

### 5.6. Cú pháp quota

Các dạng hỗ trợ:

```ini
100/1h:fixed_window
40/30m:1/45s
25/5m:sliding_window_counter
unlimited
```

Giải thích:
- `100/1h:fixed_window`: tối đa 100 mail mỗi giờ
- `40/30m:1/45s`: token bucket, capacity 40, refill 1 token mỗi 45 giây
- `25/5m:sliding_window_counter`: sliding window counter
- `unlimited`: bỏ giới hạn

### 5.7. `[AdaptiveLimits]`

Adaptive limit theo trust/risk:

```ini
[AdaptiveLimits]
enable = true
authenticated_multiplier = 1.5
unauthenticated_multiplier = 0.5
local_sender_multiplier = 1.25
external_sender_multiplier = 0.75
trusted_multiplier = 2.0
trusted_account_lists = vip_accounts
trusted_domain_lists = local_domains
trusted_ip_lists = trusted_relays
minimum_multiplier = 0.5
maximum_multiplier = 3.0
```

Ý nghĩa:
- user authenticated có thể được quota cao hơn
- sender ngoài hệ thống có thể bị siết quota
- account/domain/IP trusted được nới quota

### 5.8. `[DomainLists]`, `[AccountLists]`, `[IPLists]`

Các list có thể khai báo inline hoặc từ LDAP/file:

```ini
[DomainLists]
local_domains = example.com,internal.example
ldap_domains = ldap://ldap.example.com/dc=example,dc=com

[AccountLists]
vip_accounts = ceo@example.com,cfo@example.com

[IPLists]
trusted_relays = 10.0.0.0/24,192.168.1.10
```

Hỗ trợ:
- inline list
- `file:///path/to/file`
- `ldap://...` với domain list

### 5.9. `[Quotas]` và `[Policies]`

Map quota theo rule:

```ini
[Quotas]
internal_quota = 500/1h:fixed_window
external_quota = 150/1h:fixed_window
vip_quota = unlimited

[Policies]
vip_internal = @vip_accounts:@local_domains:vip_quota
internal_to_internal = @local_domains:@local_domains:internal_quota
internal_to_external = @local_domains:*:external_quota
```

Format policy:

```text
sender_match:recipient_match:quota_name
```

Một số matcher thường dùng:
- `*`
- `@list_name`
- domain/account trong list Redis đã load

### 5.10. `[EmailValidation]`

Validation và blacklist:

```ini
[EmailValidation]
monitor_only = false
validate_sender_syntax = true
enable_blacklist = true
validate_recipient_syntax = true
validate_recipient = true
sender_blacklist_file = /etc/policyd/blacklist_sender.txt
recipient_blacklist_file = /etc/policyd/blacklist_recipient.txt
domain_blacklist_file = /etc/policyd/blacklist_domain.txt
```

Nếu `monitor_only=true`, service chỉ log/đếm sự kiện thay vì reject.

### 5.11. `[ExternalAction]`, `[Script]` và `[Webhook]`

Lock/unlock/status chỉ đi qua external provider. Cấu hình đơn giản nhất là script:

```ini
[ExternalAction]
enable = true
provider = script
fallback_providers = webhook
continue_on_error = false
async_execution = false
async_queue_size = 1000
async_workers = 10

[Script]
lock_command = /opt/repos/policyd-py/scripts/account_action.sh lock ${email} ${reason}
unlock_command = /opt/repos/policyd-py/scripts/account_action.sh unlock ${email}
status_command = /opt/repos/policyd-py/scripts/account_action.sh status ${email}
```

Nếu muốn gọi hệ thống ngoài bằng HTTP, cấu hình thêm webhook làm primary hoặc fallback:

```ini
[Webhook]
lock_url = https://hooks.example.com/lock
unlock_url = https://hooks.example.com/unlock
auth_type = bearer
auth_token = CHANGE_ME
```

### 5.13. `[LDAP]`

Load domain/list từ LDAP:

```ini
[LDAP]
host = ldap.example.com
port = 389
use_ssl = false
bind_dn = cn=readonly,dc=example,dc=com
bind_password = CHANGE_ME
base_dn = dc=example,dc=com
search_filter = (associatedDomain=*)
domain_attribute = associatedDomain
timeout = 10
refresh_interval = 60
```

### 5.14. `[Web]`

Management API local:

```ini
[Web]
enable = true
host = 127.0.0.1
port = 8080
username = admin
password = CHANGE_ME
bearer_token =
cors_enabled = false
cors_origins = *
```

Khuyến nghị:
- chỉ bind `127.0.0.1` hoặc private IP
- bật `Basic Auth` hoặc `Bearer Token`
- không public endpoint này trực tiếp ra Internet

## 6. Management API

### 6.1. Health

```bash
curl http://127.0.0.1:8080/health
```

### 6.2. Stats

```bash
curl -u admin:CHANGE_ME http://127.0.0.1:8080/api/v1/stats
```

Trả về counters chính và metrics mới như:
- `total_sliding_window_checks`
- `total_adaptive_adjustments`
- `total_penalty_applied`
- `total_penalty_escalations`

### 6.3. Runtime state theo user

```bash
curl -u admin:CHANGE_ME http://127.0.0.1:8080/api/v1/runtime/state/user@example.com
```

Trả về:
- lock state
- lock reason
- lock TTL
- penalty count
- penalty TTL
- usage theo các quota hiện có

### 6.4. Reload config

```bash
curl -u admin:CHANGE_ME -X POST http://127.0.0.1:8080/api/v1/config/reload
```

### 6.5. Save config

```bash
curl -u admin:CHANGE_ME \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/v1/config/save \
  -d '{
    "updates": {
      "Limits": {
        "default_quota": "120/1h:fixed_window,40/5m:sliding_window_counter"
      }
    }
  }'
```

Lưu ý:
- config mới được validate trước khi replace file active
- save xong sẽ reload runtime

### 6.6. Lock/unlock user

```bash
curl -u admin:CHANGE_ME \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/v1/users/lock \
  -d '{"email":"user@example.com","reason":"manual"}'

curl -u admin:CHANGE_ME \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/v1/users/unlock \
  -d '{"email":"user@example.com"}'
```

### 6.7. Reset rate limit và penalty

```bash
curl -u admin:CHANGE_ME -X POST \
  http://127.0.0.1:8080/api/v1/ratelimit/user@example.com/reset

curl -u admin:CHANGE_ME -X POST \
  http://127.0.0.1:8080/api/v1/penalty/user@example.com/reset
```

## 7. Cấu hình production single-node khuyến nghị

### 7.1. Mức tối thiểu

- bật Redis persistence phù hợp hoặc ít nhất restart policy rõ ràng
- management API bind local/private only
- bật auth cho API
- không để `debug=true`
- dùng `ratelimit_use_lua=true`
- cấu hình `local_domains` chính xác
- test lock/unlock flow trước khi đưa vào production

### 7.2. Ví dụ quota thực dụng

```ini
[Limits]
default_quota = 100/1h:fixed_window,30/5m:sliding_window_counter

[Penalty]
enable = true
ttl = 1d
steps = 10m,30m,2h

[AdaptiveLimits]
enable = true
authenticated_multiplier = 1.5
unauthenticated_multiplier = 0.5
local_sender_multiplier = 1.25
external_sender_multiplier = 0.75
trusted_multiplier = 2.0
trusted_account_lists = vip_accounts
trusted_domain_lists = local_domains
trusted_ip_lists = trusted_relays
minimum_multiplier = 0.5
maximum_multiplier = 3.0
```

## 8. systemd sample

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
ExecStart=/opt/project/policyd/policyd-py/venv/bin/python -m policyd_py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Apply:

```bash
systemctl daemon-reload
systemctl enable --now policyd-py
systemctl status policyd-py
```

## 9. Kiểm thử

Chạy test suite:

```bash
python3 -m unittest discover -s /opt/project/policyd/policyd-py -p 'test*.py' -v
```

Hiện tại repo có:
- unit test parser/config
- handler integration test
- management service test
- multiserver manager test
- rate limiter test
- Redis Lua integration test có điều kiện `skip` nếu máy thiếu `redis-server`/`valkey-server`

## 10. Giới hạn hiện tại

Nếu chỉ chạy `single-node`, repo đã đủ dùng production ở mức thực dụng.

Các phần chưa phải trọng tâm single-node:
- orchestration multi-node hoàn chỉnh qua runtime bootstrap
- cluster API đầy đủ trong daemon chính
- Prometheus exporter riêng
- end-to-end test với Postfix thật

## 11. File quan trọng

- entrypoint: [__main__.py](/opt/project/policyd/policyd-py/policyd_py/__main__.py)
- config parser: [settings.py](/opt/project/policyd/policyd-py/policyd_py/config/settings.py)
- policy handler: [handler.py](/opt/project/policyd/policyd-py/policyd_py/policy/handler.py)
- rate limiter: [limiter.py](/opt/project/policyd/policyd-py/policyd_py/ratelimit/limiter.py)
- management API: [api_server.py](/opt/project/policyd/policyd-py/policyd_py/management/api_server.py)
- example config: [config.ini.example](/opt/project/policyd/policyd-py/config.ini.example)

## 12. Quick start ngắn

```bash
cd /opt/project/policyd/policyd-py
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
cp config.ini.example /etc/policyd/config.ini
POLICYD_CONFIG=/etc/policyd/config.ini python -m policyd_py
```
