# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `policyd_py/`. Keep related logic grouped by domain: `policy/` handles request evaluation, `ratelimit/` enforces quotas, `storage/` wraps Redis/KeyDB access, `management/` exposes the local API, and `config/` owns INI parsing plus typed settings. Runtime entry is `policyd_py/__main__.py`. Example operational scripts live in `scripts/`. Tests are in `tests/` and follow the package layout with focused integration coverage where needed.

## Build, Test, and Development Commands
Create an isolated environment and install dependencies:

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

Run the daemon locally with an explicit config:

```bash
POLICYD_CONFIG=/etc/policyd/config.ini python -m policyd_py
```

Run the full test suite with the built-in test runner:

```bash
python -m unittest discover -s tests
```

Run a targeted module while iterating:

```bash
python -m unittest tests.test_management_service
```

## Coding Style & Naming Conventions
Use Python 3.11+ style with 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and explicit type hints where interfaces cross modules. Follow the existing pattern of small, focused classes and `pydantic` models in `config/settings.py`. Keep config keys aligned with `config.ini.example`, and prefer clear logger names plus actionable error messages. No formatter or linter config is committed here, so match surrounding code closely and keep imports/std-lib ordering tidy.

## Testing Guidelines
Tests use `unittest`, including `IsolatedAsyncioTestCase` for async paths. Name files `test_*.py` and keep each test centered on one behavior. Redis-backed coverage in `tests/test_rate_limiter_redis_integration.py` requires `redis-server` or `valkey-server` on `PATH`; keep pure unit coverage separate so most changes still run quickly.

## Commit & Pull Request Guidelines
This checkout does not include `.git`, so commit conventions cannot be verified from local history. Use short, imperative subjects such as `Add webhook fallback metrics` and keep commits narrowly scoped. PRs should describe behavioral impact, config changes, and operational risks. Link related issues, list the commands you ran, and include sample API payloads or config snippets when changing management or policy behavior.

## Security & Configuration Tips
Do not commit real LDAP, webhook, script-command, or SMTP credentials. Treat `config.ini.example` as the source of truth for new settings, and document any new environment dependency such as `POLICYD_CONFIG`, Redis notifications, or external action scripts.
