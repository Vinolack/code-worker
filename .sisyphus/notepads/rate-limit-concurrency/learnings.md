# Learnings

## 2026-03-20T03:58:17Z Task: init
- Awaiting task-specific learnings.

## 2026-03-20T04:11:00Z Task: task-1-research
- Existing Redis business keys are bare names (`user-api-key`, `user-ai-model`) and already use temp->rename atomic swaps in `src/services/api_key.py` and `src/jobs/ai_model.py`.
- Queue keys are hardcoded bare names (`chat_log_queue`, `chat_log_dead_letter_queue`, `except_log_queue`) without env/service namespace.
- `src/config/config.py` extension pattern is `_get_config_value` constants; current tests do not cover config default/missing behavior for new limiter fields.
- Shared-quota requirement implies all instances must point to the same limiter Redis target and same namespace prefix.

## 2026-03-20T00:00:00Z Task: task-1-implementation
- `src/config/config.py` can safely extend new limiter fields by reading `limiter.redis.*` and defaulting to existing `redis.*`, preserving backward compatibility.
- Namespacing governance can be staged by adding static constants only (`KEY_NAMESPACE_TEMPLATE`, limiter/legacy modules, governed legacy key tuple) without changing current runtime key usage.
- Example config should include a dedicated `[limiter.redis]` section so operators can pin a shared quota Redis target explicitly even when business Redis remains unchanged.
- To keep `src/config/config.py` diagnostics clean without runtime changes, typing `_APP_CONFIG` as `Any` avoids broad pyright false-positives from dynamic TOML tree lookups.

## 2026-03-20T04:20:57Z Task: task-1-verification-fix
- Added deterministic unit test  by mocking config loading, so shared limiter Redis fallback semantics are validated without Redis/network dependencies.
- Cleaned accidental scope-creep files (, ) to keep Task 1 surface minimal and focused on config/constants groundwork.

## 2026-03-20T04:21:12Z Task: task-1-verification-fix
- Added deterministic unit test tests/limiter/test_config_defaults.py::test_shared_limiter_redis_target by mocking config loading, so shared limiter Redis fallback semantics are validated without Redis/network dependencies.
- Cleaned accidental scope-creep files (src/CLAUDE.md, .claude/index.json) to keep Task 1 surface minimal and focused on config/constants groundwork.

## 2026-03-20T04:30:07Z Task: task-1-namespace-collision-tests
- New test `tests/limiter/test_key_namespace_conflicts.py` validates legacy governed bare-key prefixes include `user-api-key` and `user-ai-model`.
- Collision check is deterministic and confirms limiter namespaced-style prefixes are excluded from `LEGACY_GOVERNED_KEY_PREFIXES`.

## 2026-03-20T00:00:00Z Task: task-2-lua-atomic-limiter
- Redis Lua脚本在双Key模型上使用同一`request_id`作为member，可天然实现owner-token约束（仅持有者可续期/释放）。
- 通过`ZREMRANGEBYSCORE`先清理过期member，再做`ZCARD`限流判断，可避免陈旧占位导致的误拒绝。
- 为避免测试环境依赖真实Redis，可对`register_script`返回的可调用对象做内存仿真，保持脚本封装层行为可确定性验证。

## 2026-03-20T04:48:50Z Task: task-3-mode-wrapper
- observe模式要做到"可观测不拦截"，在底层acquire成功时立即release可避免观察模式占用并发槽位。
- wrapper返回结构化结果（allowed/blocked/would_block/bypass/error_policy_action）后，调用方无需解析底层Lua返回语义即可做统一决策。
- 配置读取沿用`getattr(config, ..., default)`可兼容Task1尚未补齐常量的场景，避免引入额外配置耦合。

## 2026-03-20T00:00:00Z Task: task-4-api-auth-model-cache-reservation
- 在`CodePlanApiKeyDigestOnlyResp`与`CodePlanApiKeyPermResp`新增可选`user_id`与动态并发字段，可无侵入兼容旧响应。
- 鉴权缓存写入统一走模型序列化后，新增字段在存在时可透传，缺失时保持`None`回退。
- 通过Fake Redis + monkeypatch可稳定验证ApiKeyService解析/缓存回读，无需真实Redis与网络依赖。

- Task5: 在  新增  与统一解析函数，默认 subject 固定回落到 。
- 预留开关采用  读取，默认关闭时不会改变当前限流口径。

- Task5补充: 在src/services/vlm.py新增LimiterResolutionPolicy与统一解析函数，默认subject回落到api_key_digest。
- Task5补充: 预留开关通过getattr(config, ...)读取，默认关闭时不改变当前口径。

## 2026-03-20T05:42:34Z Task: task-6-pre-submit-acquire
- 在 `VlmService` 里抽出 `_acquire_pre_submit_limiter` 后，可复用同一套 pre-submit 限流逻辑覆盖 chat/responses/messages 多入口，避免路径间行为漂移。
- 将 `subject_key/model_name/request_id` 结构化写入 `user_data['limiter_context']`，可在后续 Task10 无侵入挂接 release 生命周期。

## 2026-03-20T06:02:13Z Task: task-7-audio-limit-paths
- 音频路由预检放在读取上传文件前可在高并发压测下更早返回429，避免无意义大文件读入。
- 预检采用独立占位并在enforce+granted时立即release，可实现快速探测而不污染正式提交并发槽位。
- 音频non-stream与stream提交链路复用后，与chat/responses/messages限流语义保持一致。

## 2026-03-20T06:02:43Z Task: task-7-audio-limit-paths-fix
- 音频non-stream与stream提交链路统一复用VlmService._acquire_pre_submit_limiter，行为与chat/responses/messages保持一致。

## 2026-03-20T06:19:22Z Task: task-8-image-limit-release-order
- image stream/non-stream 已复用 `_get_model_config(..., include_limiter_policy=True)` + `_acquire_pre_submit_limiter`，在 submit 前完成分布式限流 acquire。
- 通过 `_image_generation_limiter_contexts` 按 `proxy req_id` 交接 limiter token，上游流消费结束后再释放，避免 do_request/get_response 双释放。
- `_release_limiter_context_once` 以 `release_required/release_state` 做一次性释放门禁，保证本地 semaphore 与分布式 token 生命周期解耦。


## 2026-03-20T06:31:31Z Task: task-9-non-chat-limit-paths
- `proxy_request_non_stream`（embeddings/rerank共享链路）与 `proxy_tts` 复用 `include_limiter_policy=True` + `_acquire_pre_submit_limiter` 后，可与 chat/audio 保持一致的 pre-submit 拒绝语义。
- 新增 `tests/limiter/test_non_chat_paths_limits.py` 通过 Fake limiter/proxy 做确定性断言，覆盖 blocked/allowed/observe/off/fail-open 与 limiter_context 透传。

## 2026-03-20T06:55:00Z Task: task-10-stream-release-semantics
- 将 limiter release 收口到 worker 终态回调链（success/failure/cancel）后，stream 下游 generator 结束不再成为主释放触发点。
- image stream 新增 `image_stream_handoff` + inflight 兜底判活分支，可避免客户端断连时提前释放，同时兼容无 worker 回调的测试桩场景。
- `_release_limiter_context_once` 的 `release_state` 门禁可承受“回调触发 + 兜底触发”重复调用，保证 exactly-once 语义。

## 2026-03-20T07:06:14Z Task: task-11-ai-model-sync-default-concurrency
- `SyncAiModelJob.load_ai_models_to_redis` 现在显式保留并传播 `model_default_user_total_concurrency_limit` 与 `model_default_user_model_concurrency_limit`，主键/alias/lowercase 三类记录字段一致。
- 旧 payload 缺失上述字段时，sync 继续成功，Redis 记录不引入额外字段，保持向后兼容。

## 2026-03-20T07:18:13Z Task: task-12-auth-contract-reservation
- `ApiKeyService._remote_auth_check` 增加 `CodePlanApiKeyPermResp` 归一化，确保 perm 返回里的 `user_id` 与并发字段在缓存前先完成兼容解析。
- 新增 `tests/limiter/test_backend_contract_limits.py` 通过 fake http + fake redis 串联验证 digest-only/perm 的 remote -> cache -> readback 路径，避免只测单点缓存读取。
- 在 `LIMITER_SUBJECT_USE_USER_ID` 和 `LIMITER_ENABLE_DYNAMIC_LIMITS` 关闭时，即使鉴权返回预留字段，`VlmService` 仍按 model/local 默认限额生效。

## 2026-03-20T07:29:13Z Task: task-14-automated-tests
- 双维并发集成测试可通过“同模型并发竞争 + 跨模型总量封顶 + 显式释放后恢复”在单测内稳定复现，无需真实 Redis。
- lease 恢复测试里用离散 `now_ms` 驱动 TTL，能稳定覆盖泄漏占位、迟到释放、续租延长窗口三类恢复语义。
- 流式边界补充 `_release_limiter_context_once` 幂等断言后，可直接防回归“回调重复触发导致二次释放”风险。

## 2026-03-20T07:34:08Z Task: task-13-runtime-policy-metrics
- `src/config/config.py` 增加了 limiter 运行模式/故障策略/租约TTL与开关字段的显式配置解析，并保留多种旧键名回退，兼容灰度切换。
- `ConcurrencyLimiterService` 新增轻量事件计数与可选事件 hook，可观测 `acquire/blocked/release/redis_error` 四类关键事件而不阻塞主链路。
- `tests/limiter/test_redis_failure_policy.py` 用 fake redis 覆盖了 `off/observe/enforce` 语义和 fail-open/fail-closed 行为，保证 Redis 故障策略可回归。

## 2026-03-20T07:41:25Z Task: task-15-ci-limiter-gate
- 在 `.gitlab-ci.yml` 新增 `test` stage 与 `limiter_regression_gate` 作业，固定执行 `pytest -q tests/limiter` 作为 limiter 回归门禁。
- 门禁作业规则与现有 `build_image` 分支策略对齐（dev/beta/main，排除 web/schedule），减少流水线触发语义漂移。
- 本地执行 `pytest -q tests/limiter` 稳定通过（79 passed, 1 warning），可直接镜像到 CI 验证流。

## 2026-03-20T08:20:00Z Task: final-wave-f1-remediation
- legacy业务键治理可在运行时用`env:service:legacy:<bare-prefix>`落地，并通过读路径回退旧裸键实现无停机兼容。
- `ApiKeyService` 与 `SyncAiModelJob` 改为只写治理键后，避免继续污染裸命名空间；`VlmService` 读模型配置可优先治理键再fallback。
- limiter rollout 用 `sha256(user_id)` 固定分桶可得到稳定灰度人群，`rollout_percent` 外的请求直接 bypass 且不触发 Redis 操作。


## 2026-03-20T08:50:28Z Task: final-wave-f3-rerun
- Re-ran required limiter regression suites (test_mode_switch.py, test_subject_resolution.py, test_api_key_limit_fields.py, test_concurrency_integration.py, test_lease_recovery.py) with 21/21 pass.
- Re-ran full limiter suite (pytest -q tests/limiter) for regression confidence with 85/85 pass.
- Rollout behavior checks passed (test_rollout_bypass_outside_hash_bucket_avoids_redis, test_rollout_in_hash_bucket_still_enforces_limits).
- Governed legacy fallback checks passed (test_model_namespace_falls_back_to_legacy_bare_key, test_digest_only_cache_falls_back_to_legacy_bare_key).
- Representative happy/blocked/edge paths remain stable after remediation; verdict APPROVE with no behavior regression found.

## 2026-03-20T08:56:13Z Task: final-wave-f2-rerun-post-remediation
- F2 rerun confirms merge-quality signal should prioritize `tests/limiter` and changed-file diagnostics: limiter suite stayed at `85 passed` after remediation.
- `src/services/vlm.py` Pyright findings remain baseline-wide technical debt; changed companion files (`src/services/concurrency_limiter.py`, `src/services/api_key.py`, `src/jobs/ai_model.py`, `src/config/config.py`, `src/base/constants/const.py`, `src/routers/vlm.py`, `src/api_models/api_key/resp_model.py`) are diagnostics-clean.
- Full `pytest -q tests` is still influenced by external-network timeout tests (`tests/test_chat.py`), so these failures should be tracked as environment baseline unless limiter-path coupling evidence appears.

## 2026-03-20T09:19:15Z Task: task-config-example-limiter-docs
- `config.example.toml` 的 limiter 示例应与 `src/config/config.py` 读取键一一对应，至少覆盖 mode/fail_policy/lease_ttl_ms、主体与动态开关、rollout 与 limiter.redis 全套连接键。
- 注释需同时给出单位与生效口径：`lease_ttl_ms` 用毫秒，默认主体口径为 `api_key_digest`，开启 `subject_use_user_id` 才切换为 `user_id`。
- 在共享 Redis 场景，`limiter.redis.prefix/env/service` 是命名空间治理核心，示例配置应明确提示必须规避键冲突。

## 2026-03-20T09:27:56Z Task: task-limiter-lease-ttl-900s
- limiter 租约 TTL 默认值需在配置层（`src/config/config.py`）和服务兜底层（`src/services/vlm.py`）同步修改，避免双默认值漂移。
- 为覆盖最长 800 秒请求并降低超发风险，`lease_ttl_ms` 基线统一到 `900000`（900 秒），且示例配置应与运行时默认一致。
- 该调整仅针对 limiter 租约语义，不应波及鉴权/模型缓存 TTL（如 api_key 缓存 300 秒）。

## 2026-03-20T10:15:00Z Task: task-limiter-lifecycle-logging
- 请求开始判定日志应稳定落在 `_acquire_pre_submit_limiter` 的 `acquire.start/acquire.result/acquire.would_block/acquire.blocked`，并统一携带 `req_id/trace_id/subject/model/policy/mode/request_id`。
- 请求结束判定日志应落在 `_finalize_limiter_and_image_slot_once` 与 `_release_limiter_context_once`，形成 `request.end -> release.result|release.skip` 闭环，且 release 幂等跳过路径需保留 debug 级可观测性。
- `AsyncHttpClient` 的 worker 终态（success/failed/cancelled/finally）日志可补齐 submit 之后的可追踪链路，便于跨模块按 `request_id + req_id + trace_id` 串联排障。
