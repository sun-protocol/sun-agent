# SunAgent

[![Checks](https://github.com/TronNova/SunAgent/actions/workflows/checks.yml/badge.svg)](https://github.com/TronNova/SunAgent/actions/workflows/checks.yml)

SunAgent is a multi-agent project for X(Twitter) automation.

It currently includes two runnable sample applications:

- `samples/twitter_robot`: Twitter AI bot (mentions reply, timeline analysis, scheduled posting)
- `samples/token_robot`: Web3 token bot (interaction + token-launch related workflow)

## What This Repo Contains

- `packages/sunagent-app`: business-level agents, templates, metrics, services
- `packages/sunagent-ext`: shared extensions (memory, cache store, tweet helpers, utilities)
- `samples/twitter_robot`: runnable Twitter bot entry script and env template
- `samples/token_robot`: runnable token bot entry script and env template
- `tools/twitter_oauth.py`: helper script to get Twitter OAuth 1.0a access token/secret

## Requirements

- Python `>=3.12` (workspace root uses uv; `sunagent-ext` itself supports 3.10+)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Chrome/Chromium available (used by Playwright in twitter bot flow)

Optional system dependency (used in some image-related flows):

```bash
sudo apt-get install libavif16
```

## Quick Start

```bash
git clone https://github.com/sun-protocol/SunAgent.git
cd SunAgent
uv sync --all-extras
source .venv/bin/activate
```

## Run The Samples

### 1) Twitter Bot

```bash
cd samples/twitter_robot
cp .env.twitter.example .env
# edit .env
python twitter_app.py
```

Default HTTP port: `9529`.

### 2) Token Bot

```bash
cd samples/token_robot
cp .env.token.example .env
# edit .env
python token_launch_app.py
```

Default HTTP port: `9530` in sample env template.

## Configuration Reference

Both samples load config from `.env` via `python-dotenv`.

### Core / Common

| Variable | Required | Description |
| --- | --- | --- |
| `AGENT_ID` | Yes | Agent identifier (used in app naming/cache keys). |
| `LOGGING_CONFIG` | No | Logging yaml path. Default: `logging_config.yaml`. |
| `HTTP_PORT` | No | Quart HTTP listen port. |
| `TW_TIMEOUT` | No | Twitter API timeout seconds. Default `60`. |

### OpenAI (Azure-style settings used by code)

| Variable | Required | Description |
| --- | --- | --- |
| `OPENAI_MODEL` | Yes | Model name passed to client. |
| `OPENAI_ENDPOINT` | Yes | Azure OpenAI endpoint. |
| `OPENAI_DEPLOYMENT` | Yes | Azure deployment name. |
| `OPENAI_API_VERSION` | Yes | API version. |
| `OPENAI_API_KEY` | Yes | API key. |

### Twitter API

| Variable | Required | Description |
| --- | --- | --- |
| `TW_CONSUMER_KEY` | Yes | Twitter app consumer key. |
| `TW_CONSUMER_SECRET` | Yes | Twitter app consumer secret. |
| `TW_ACCESS_TOKEN` | Yes | User access token. |
| `TW_ACCESS_TOKEN_SECRET` | Yes | User access token secret. |
| `TW_BEARER_TOKEN` | Recommended | Needed for bearer-token based reads/streaming in sample flows. |

### Cache / Memory

| Variable | Required | Description |
| --- | --- | --- |
| `REDIS_URL` | Optional | Enable Redis cache when set. |
| `REDIS_EXPIRE` | No | Cache expiration seconds (for RedisStore). |
| `MEM0X_NAME` | Optional | mem0x memory config (if you enable mem0x workflows). |
| `MEM0X_HEAD` | Optional | mem0x memory config. |
| `MEM0X_URL` | Optional | mem0x memory config. |
| `MEM0X_TABLE_NAME` | Optional | mem0x memory config. |
| `MEMORY_AGENT_ID` | Optional | mem0x memory config. |
| `MEMORY_LIMIT` | No | Memory recall limit (template default: `50`). |
| `MEMORY_SCORE_THRESHOLD` | No | Memory score threshold (template default: `0.5`). |

### Twitter Bot Scheduling (`samples/twitter_robot`)

| Variable | Required | Description |
| --- | --- | --- |
| `HOME_TASK_INTERVAL_MINUTES` | No | Home timeline polling interval. Default `60`. |
| `MENTIONS_TASK_INTERVAL_MINUTES` | No | Mentions polling interval. Default `2`. |
| `FLASH_TASK_HOUR` | No | Cron hour window for flash-news task. Default `10-20`. |
| `FLASH_TASK_MINUTES` | No | Cron minute expression for flash-news task. Default `*/20`. |
| `NEWS_URLS` | No | JSON string list of flash/news URLs. |
| `BLOCK_USER_IDS` | No | Blocklist of user IDs in logic. |
| `ROBOT_FREQ_LIMIT` | No | Frequency limit used by agent logic. |

### Browser / Playwright (`samples/twitter_robot`)

| Variable | Required | Description |
| --- | --- | --- |
| `BROWSER` | Often yes | Chrome executable path (sample provides Linux/macOS examples). |
| `BROWSER_DATA_DIR` | Often yes | Chrome user data dir for persistent context. |

### Token Bot Specific (`samples/token_robot`)

| Variable | Required | Description |
| --- | --- | --- |
| `TOKEN_LAUNCH_INTERVAL_SECONDS` | No | Mentions/launch interval. Default `90`. |
| `SUNPUMP_HOST` | Optional | SunPump endpoint config used by token workflow. |
| `SUNPUMP_OPS_HOST` | Recommended | Ops service host used by `SunPumpService`. |
| `SUNPUMP_API_HOST` | Optional | Additional API host config. |
| `PUMP_MCP_URL` | Optional | MCP SSE endpoint for external tools. |

## HTTP API (Sample Apps)

### `samples/twitter_robot/twitter_app.py`

- `POST /tweet`
  - Behavior: forwards JSON body to `context_builder.create_tweet(data)`
  - Response shape: `{"error_code": <int>, "text": <string>}`

### `samples/token_robot/token_launch_app.py`

- `POST /tweet`
  - Behavior: forwards JSON body to `context_builder.create_tweet(data)`
  - Response shape: `{"error_code": <int>, "text": <string>}`

- `POST /recover_time`
  - Input: `{"timestamp": <int>}`
  - `timestamp <= 0`: unset recover time
  - `timestamp > 0`: set recover time

## Scheduling Behavior

### Twitter Bot

On startup it registers jobs:

- `news_flash_task`: cron by `FLASH_TASK_HOUR` and `FLASH_TASK_MINUTES`
- `home_timeline_task`: interval by `HOME_TASK_INTERVAL_MINUTES`
- `mentions_timeline_task`: interval by `MENTIONS_TASK_INTERVAL_MINUTES`

All jobs are configured with `max_instances=1` to avoid overlap.

### Token Bot

On startup it registers jobs:

- `mentions_task`: interval by `TOKEN_LAUNCH_INTERVAL_SECONDS`
- `promote_task`: cron at UTC+8 Monday/Wednesday/Friday 12:00
- `show_case_task`: cron at UTC+8 Tuesday/Thursday 12:00

## Development

Create and activate workspace environment:

```bash
uv sync --all-extras
source .venv/bin/activate
```

Run checks:

```bash
uv run poe lint
uv run poe pyright
uv run poe mypy
uv run poe test
```

Build all packages:

```bash
uv build --all-packages --wheel --out-dir dist
```

## Observability

Metrics are defined in `packages/sunagent-app/src/sunagent_app/metrics.py`.

Examples include:

- tweet read/post success/failure counters
- model API success/failure counters
- twitter quota gauges
- request count and duration histograms

`start_metrics_server` is exported by `sunagent_app` as a thin alias to Prometheus `start_http_server`.

## Troubleshooting

### 1) Browser startup fails in Twitter bot

- Verify `BROWSER` points to a real Chrome/Chromium executable.
- Verify `BROWSER_DATA_DIR` is writable and points to a valid profile directory.
- On Linux servers, ensure runtime dependencies for Chrome are installed.

### 2) Twitter request/auth errors

- Confirm all `TW_*` keys are from the same Twitter app/user context.
- If read/stream flows fail, ensure `TW_BEARER_TOKEN` is set.
- For token acquisition, use helper: `python tools/twitter_oauth.py`.

### 3) LLM call failures

- Check `OPENAI_*` values (`ENDPOINT`, `DEPLOYMENT`, `API_VERSION`, `API_KEY`).
- Verify model/deployment names match your Azure resource.

### 4) Redis cache not taking effect

- Ensure `REDIS_URL` is set and reachable.
- Check `REDIS_EXPIRE` format (integer seconds).

### 5) Port already in use

- Change `HTTP_PORT` in `.env`.
- Ensure old sample process is stopped before restart.

## Security Notes

- Never commit `.env` with real credentials.
- Prefer least-privilege API keys and rotate secrets regularly.
- In production, run behind reverse proxy and restrict public access to bot control endpoints.
