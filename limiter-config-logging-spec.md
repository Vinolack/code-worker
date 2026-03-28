# 限流配置说明与日志规范清单

## 1. limiter 配置示例（TOML）

```toml
[limiter]
mode = "enforce"
fail_policy = "fail-open"
lease_ttl_ms = 900000
user_total_concurrency_limit = 20
user_model_concurrency_limit = 5
subject_use_user_id = false
enable_dynamic_limits = false
rollout_percent = 100

[limiter.redis]
host = "10.85.3.196"
port = 6379
password = ""
db = 2
prefix = "xaio:limiter"
env = "prod"
service = "xaio-code-worker"
```

说明：当前基线为 `lease_ttl_ms=900000`，即租约有效期 900 秒。

## 2. 关键配置项语义说明

- `mode`：限流运行模式。`off` 关闭，`observe` 仅观测不拦截，`enforce` 严格拦截。
- `fail_policy`：Redis 故障时策略。`fail-open` 放行请求，`fail-closed` 拒绝请求。
- `lease_ttl_ms`：并发租约 TTL，单位毫秒。请求拿到并发令牌后，租约在该时长内有效。
- `subject_use_user_id`：主体口径开关。`false` 以 `api_key_digest` 作为限流主体，`true` 以 `user_id` 作为限流主体。
- `enable_dynamic_limits`：是否启用外部动态限额。关闭时使用静态配置，开启时可按用户覆盖默认并发上限。
- `rollout_percent`：灰度百分比，范围 `0-100`。用于按比例启用限流逻辑。
- `limiter.redis.host`：限流 Redis 主机地址。
- `limiter.redis.port`：限流 Redis 端口。
- `limiter.redis.password`：限流 Redis 密码。
- `limiter.redis.db`：限流 Redis 库编号。
- `limiter.redis.prefix`：限流键前缀，用于隔离命名空间。
- `limiter.redis.env`：环境段，建议与部署环境一致，如 `prod`、`staging`。
- `limiter.redis.service`：服务段，建议固定为服务名，避免共享 Redis 时键冲突。

## 3. TTL 语义区分

- 租约 TTL：由 `lease_ttl_ms` 控制，作用对象是并发租约键。请求异常退出时，租约会在 TTL 到期后自动释放，避免并发位长期占用。
- 缓存 TTL：作用对象是缓存数据，不是并发租约。典型如鉴权缓存、权限缓存等，常见单位为秒，由各缓存模块独立控制。
- 运维排障时要分开看：并发位不释放优先检查租约 TTL，缓存命中异常优先检查缓存 TTL。

## 4. 日志事件规范

### 4.1 事件清单

| 事件名 | 建议级别 | 触发时机 | 必填字段 |
| --- | --- | --- | --- |
| `Limiter acquire.start` | INFO | 限流申请开始 | `request_id`, `trace_id`, `subject`, `model`, `mode`, `rollout_percent`, `ttl_ms` |
| `Limiter request.end` | INFO | 请求结束，进入释放阶段 | `request_id`, `trace_id`, `subject`, `model`, `status`, `duration_ms`, `mode` |
| `http.worker.terminal.success` | INFO | 上游 HTTP 请求最终成功返回 | `request_id`, `trace_id`, `url`, `method`, `status_code`, `duration_ms`, `attempt` |

### 4.2 字段规范

- `request_id`：单次请求唯一标识。
- `trace_id`：链路追踪标识，跨服务关联排障。
- `subject`：限流主体，取值来源受 `subject_use_user_id` 影响。
- `model`：模型名或模型别名。
- `mode`：当前限流模式，便于识别是否处于观测或强拦截。
- `rollout_percent`：当前灰度比例，便于判断是否命中灰度。
- `ttl_ms`：本次租约 TTL，要求与运行配置一致，当前基线应为 `900000`。
- `duration_ms`：阶段耗时，单位毫秒。
- `status`：阶段状态，如 `success`、`blocked`、`error`。
- `attempt`：HTTP 重试次数，从 1 开始。

## 5. 告警建议

1. 限流拦截率告警：5 分钟窗口内 `Limiter request.end` 中 `status=blocked` 比例超过 20% 触发告警，连续 3 个窗口升级。
2. 降级风险告警：出现 Redis 不可用且策略走 `fail-open` 时触发高优先级告警，提示限流保护已降级。
3. 上游稳定性告警：`http.worker.terminal.success` 事件缺失或延迟突增时告警，结合 `duration_ms` 的 P95/P99 阈值定位上游抖动。

## 6. 交付与使用建议

- 运维上线前先核对 `mode`、`fail_policy`、`lease_ttl_ms` 三个开关，确保与发布策略一致。
- 研发排障时优先按 `request_id` 和 `trace_id` 串联 `Limiter acquire.start`、`Limiter request.end`、`http.worker.terminal.success`。
- 共享 Redis 场景必须维护 `prefix/env/service`，避免跨环境、跨服务键冲突。
