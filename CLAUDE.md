# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **xaio-coder-worker**, a FastAPI-based VLM (Vision Language Model) proxy service that routes AI model requests to various backend providers. The service handles chat completions, embeddings, audio transcription, image generation, TTS, and reranking requests while managing authentication, logging, and model configuration synchronization.

## Common Development Commands

### Running the Application

```bash
# Direct execution
python main.py

# With Docker
docker build -t xaio-coder-worker .
docker run -d -p 8084:8088 -v $(pwd)/logs:/app/logs -v $(pwd)/config.example.toml:/app/config.example.toml xaio-coder-worker
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_chat.py

# Run tests with asyncio support
pytest tests/test_chat.py -v
```

### Dependencies

```bash
# Install dependencies
pip install -r requirements.txt

# Using virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

## Architecture Overview

### Request Flow

1. **Client Request** → **Middleware** (logging, auth) → **Router** → **Service** → **SseProxyClient** → **Upstream AI Provider**
2. Responses flow back through the same chain, with logging callbacks recording usage metrics to Redis
3. Background jobs periodically sync API keys and model configurations from upstream management service

### Core Components

**Server Initialization** (`src/server.py`):
- FastAPI app with lifespan management
- Startup: initializes Redis, VlmService, registers routers, starts scheduler
- Shutdown: closes VlmService, stops scheduler
- Middleware registration for logging and request tracking
- Exception handlers for standardized error responses

**VLM Service** (`src/services/vlm.py`):
- Central proxy service using shared `SseProxyClient` for connection pooling
- Handles both streaming and non-streaming requests
- Model configuration lookup from Redis (supports model aliasing and transformation)
- Request callbacks for logging: `_request_success_callback`, `_request_error_callback`, `_request_success_callback_stream`
- Specialized handlers for different endpoints (chat, embeddings, audio, images, TTS)
- Concurrency control for resource-intensive operations (e.g., image generation limited to 2 concurrent requests)

**SSE Proxy Client** (`src/utils/sse_proxy_client.py`):
- Async HTTP client wrapper for managing upstream requests
- Handles both streaming (SSE) and non-streaming responses
- Request queuing, retry logic, and timeout management
- Callback system for success/failure handling
- Connection pooling and lifecycle management

**Routers** (`src/routers/`):
- `vlm.py`: All VLM proxy endpoints (chat completions, embeddings, audio, images, TTS, rerank, models)
- `chat_app.py`: Additional chat application endpoints
- API key extraction from Authorization header using dependency injection

**Background Jobs** (`src/jobs/`):
- `scheduler.py`: APScheduler-based task manager
- `api_key.py`: Syncs valid API keys from upstream service
- `ai_model.py`: Syncs AI model configurations (aliases, base URLs, API keys)
- `chat_log.py`: Reports usage logs from Redis to upstream service (main queue + DLQ retry)

**Data Access** (`src/dao/`):
- `redis/`: Redis client wrapper and managers
- `managers/chat_log.py`: Chat log storage operations in Redis

**Base Utilities** (`src/base/`):
- `logging/`: Loguru-based logging with trace ID support
- `redis/`: Base Redis manager
- `exceptions/`: Custom exception classes (HttpException)
- `utils/`: Crypto, UUID, context (trace/request ID tracking)
- `constants/`: Redis key prefixes and other constants
- `enums/`: Error codes and status enums

### Configuration

Configuration is managed via `config.example.toml`:
- Server settings (host, port, log level)
- Database connections (MySQL, Redis)
- Authentication whitelists
- VLM proxy settings (retries, timeouts)
- Background job intervals (API key sync, model sync, log reporting)
- Encryption keys for worker-to-worker communication

Copy `config.example.toml` to `config.toml` (or map it via Docker volume) and update values for your environment.

### Model Configuration System

Models are stored in Redis under `USER_AI_MODEL_SET_PREFIX` as hash entries:
- Key: model alias (used by clients)
- Value: JSON with `api_key`, `base_url`, `real_ai_model`, `need_transform`, etc.
- If `need_transform=1`, requests are routed to `transform_base_url` with `transform_api_key`
- `is_display_in_worker` flag controls visibility in `/v1/models` endpoint

### Request Logging Flow

1. Service methods attach callbacks to `RequestWrapper`
2. On success/failure, callbacks extract usage data (prompt_tokens, completion_tokens)
3. Logs are stored in Redis queue via `ChatLogRedisManager.add_chat_log`
4. Background job `SyncChatLogJob` periodically sends batches to upstream service
5. Failed uploads go to dead-letter queue (DLQ) for retry

### Middleware Chain

1. **LoggingMiddleware**: Logs all requests/responses with trace IDs
2. **Request ID injection**: `TraceUtil.set_req_id()` generates unique request IDs
3. **Authentication middleware** (via `depends.py`): Validates API keys for non-whitelisted routes
4. **Error handlers** (`error_handler.py`): Catches exceptions and returns standardized JSON responses

## Important Notes

- **Redis is critical**: Application won't start without Redis connection. All model configs and API keys are cached in Redis.
- **Streaming responses**: Use `keep_content_in_memory=True` in RequestWrapper when you need to parse SSE chunks for logging.
- **Trace IDs**: All log entries include trace_id and req_id for distributed tracing. Set via `TraceUtil.set_trace_id()` and `TraceUtil.set_req_id()`.
- **Retry logic**: Configured per endpoint via `VLM_PROXY_RETRIES` and `VLM_PROXY_RETRY_INTERVAL`.
- **Concurrency limits**: Image generation uses `BoundedSemaphore(2)` to prevent overwhelming upstream providers.
- **Model aliasing**: Clients use model aliases; the service resolves to `real_ai_model` before forwarding to providers.

## CI/CD Pipeline

The project uses GitLab CI with the following stages:
1. **ai-review**: Automated AI code review
2. **build**: Docker image build for dev/beta/main branches
3. **versioning**: Tags main branch images with date-based versions (vYYYYMMDD)
4. **deploy**: Automatic deployment to dev/beta/production environments

Deployments map config file and logs directory as volumes.

## Testing Strategy

Tests are located in `tests/` directory:
- `test_chat.py`: Chat completion endpoint tests
- `test_dify.py`: Dify integration tests
- Use `pytest-asyncio` for async test functions
- Tests should mock Redis and upstream HTTP calls to avoid external dependencies
