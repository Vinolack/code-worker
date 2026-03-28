# Decisions

## 2026-03-20T03:58:17Z Task: init
- Awaiting task-specific decisions.

## 2026-03-20T04:11:00Z Task: task-1-research
- Task 1 will prioritize config/constant groundwork: add limiter Redis target fields + namespace governance constants before any limiter logic edits.
- Namespace convention baseline: `env:service:module:*` for new limiter keys; legacy bare keys will get prefixed migration strategy with compatibility fallback.
- Keep existing main Redis path intact while introducing dedicated limiter Redis target config to guarantee global shared quota pool across instances.

## 2026-03-20T00:00:00Z Task: task-1-implementation
- Added limiter target fields under `limiter.redis` with fallback defaults to current `redis` settings to avoid any deployment breakage.
- Added namespace governance constants in `src/base/constants/const.py` as additive-only changes; no existing constant names or values were changed.
- Used explicit limiter namespace settings (`prefix`, `env`, `service`) in `config.example.toml` to prepare subsequent limiter key-model tasks.
- Kept static typing stable by declaring `_APP_CONFIG: Any` in config loader rather than refactoring all dynamic getter call sites.

## 2026-03-20T00:00:00Z Task: task-2-lua-atomic-limiter
- 在`src/services/concurrency_limiter.py`内新增独立服务，不接入现有VLM请求链路，满足任务边界“仅实现原子脚本与键模型”。
- Key模型统一采用`LIMITER_REDIS_PREFIX + env:service:module + limiter scope`命名，不复用任何legacy bare key。
- release设计为幂等：无论token是否已释放都返回ok，同时通过按`request_id`定向`ZREM`保证不会误删其他owner占位。

## 2026-03-20T04:48:50Z Task: task-3-mode-wrapper
- 在`src/services/concurrency_limiter.py`保留Task2原有`acquire/release/renew`低层方法不变，新增`acquire_with_mode`作为模式/故障策略封装入口。
- mode默认值采用`enforce`，fail policy默认值采用`fail-open`，并兼容`open/closed`别名输入。
- Redis异常统一映射为`reason=redis_error`并通过`error_policy_action`标记是`fail_open_allow`或`fail_closed_block`。

## 2026-03-20T00:00:00Z Task: task-4-api-auth-model-cache-reservation
- 本任务仅做字段预留与缓存透传，不切换限流主体到`user_id`、不引入动态限流执行逻辑。
- 在`tests/limiter/test_api_key_limit_fields.py`同时覆盖“字段存在”与“字段缺失”路径，确保向后兼容语义可回归。

- 选择在  内构建但不执行限流策略，仅做抽象预埋，避免提前改动请求路径限流行为。
- 动态限额开关命名为 ，主体切换开关命名为 ，默认 false。

- Task5补充: 选择在_get_model_config内构建但不执行限流策略，只做抽象预埋，避免提前改动请求路径。
- Task5补充: 开关命名采用LIMITER_ENABLE_DYNAMIC_LIMITS与LIMITER_SUBJECT_USE_USER_ID，默认false。

## 2026-03-20T05:42:34Z Task: task-6-pre-submit-acquire
- 采用 `_get_model_config(..., include_limiter_policy=True)` 直接消费 Task5 scaffold 输出，避免新增第二条 subject/limit 解析路径。
- pre-submit 拦截仅在 `ModeAwareAcquireResult.blocked=True` 时抛 `HttpException(code=429)`，其余 allow/observe/off/fail-open 全量透传既有请求链路。

## 2026-03-20T06:02:13Z Task: task-7-audio-limit-paths
- 路由预检仅使用本地fallback的构建轻量策略，避免为预读阶段引入模型级重查开销。
- 预检key固定为并将对齐，确保只在user-total压力下快速拒绝。
- 提交阶段仍在执行权威acquire，保证最终限流判定与既有主链路一致。

## 2026-03-20T06:02:43Z Task: task-7-audio-limit-paths-fix
- 路由预检键名固定为audio_router_precheck，且user_model_limit对齐user_total_limit，确保预检聚焦user-total压力。
- 预检采用本地LIMITER_USER_TOTAL_CONCURRENCY_LIMIT回退值，避免预读阶段触发模型配置重查。
- 提交阶段在audio_transcriptions_non_stream和audio_transcriptions_do_request继续执行权威acquire。

## 2026-03-20T06:19:22Z Task: task-8-image-limit-release-order
- image 路径不改全局回调框架，采用 image 专属 release 时序：stream 在 `get_response.finally` 释放分布式 token 后再释放本地 semaphore，non-stream 在函数 finally 同序释放。
- submit 失败/断连路径统一由 do_request/non-stream finally 兜底释放，handoff 成功后由 inflight + context map 接管，避免重复 release。


## 2026-03-20T06:31:31Z Task: task-9-non-chat-limit-paths
- embeddings/rerank 继续走既有 `proxy_request_non_stream` 共享路径，不在 router 层复制限流逻辑，减少重复实现。
- TTS 仅在提交前接入 acquire 与 `limiter_context` 透传，不改请求载荷与协议转换行为，保持与既有上游兼容。

## 2026-03-20T06:55:00Z Task: task-10-stream-release-semantics
- 统一以回调链作为主释放机制：所有携带 `limiter_context` 的终态回调在 `finally` 调用 `_finalize_limiter_and_image_slot_once`，确保 success/failure/cancel 都走同一收口。
- image stream 保留“判活后兜底释放”分支，仅在 worker 不存活且回调未接管时触发，防止测试桩/异常路径导致 inflight 悬挂。
- non-stream image 的 submit/upstream 异常路径增加显式 release 兜底，并依赖 `release_state` 保证与回调重复触发时仍幂等。

## 2026-03-20T07:06:14Z Task: task-11-ai-model-sync-default-concurrency
- 采用 `SyncAiModelJob._copy_default_concurrency_fields` 作为单点字段传播，避免未来主记录/alias/lowercase 在字段复制上发生漂移。
- 保持缺省兼容策略：仅在源 payload 存在字段时透传，不为旧 payload 人工补值。

## 2026-03-20T07:18:13Z Task: task-12-auth-contract-reservation
- 保持 `src/api_models/api_key/resp_model.py` 字段定义不变，仅在 `src/services/api_key.py` 增加 perm payload 归一化，避免重复改动已稳定的契约模型。
- Task 12 范围内不启用动态限额生效，只验证预留字段可解析缓存并与现有限流默认口径共存。

## 2026-03-20T07:29:13Z Task: task-14-automated-tests
- 并发与恢复覆盖新增为两个独立测试文件（`test_concurrency_integration.py`、`test_lease_recovery.py`），避免在既有 Lua/模式测试里混入过长场景。
- 继续沿用 in-memory FakeRedis + 显式 `now_ms` 驱动，确保 race/recovery 断言可重复且不依赖真实时钟与外部服务。
- 对流式释放边界仅做增量强化（新增幂等 case），不改动原有通过路径断言与失败场景语义。

## 2026-03-20T07:34:08Z Task: task-13-runtime-policy-metrics
- 保持默认策略与历史行为一致：`LIMITER_MODE` 默认 `enforce`、`LIMITER_FAIL_POLICY` 默认 `fail-open`，仅新增显式配置入口与兼容旧键名回退。
- 事件可观测性落在 limiter service 内部（计数器 + 可选 hook），不改请求主链路协议，避免给 `VlmService` 增加阻塞型采集开销。
- 新增独立测试文件验证故障策略/模式语义，不改已有 mode tests，降低与已完成任务用例互相干扰风险。

## 2026-03-20T07:41:25Z Task: task-15-ci-limiter-gate
- 采用新增 `test` stage 承载 limiter 回归门禁，保证测试失败时直接阻断后续 `build` 与 `deploy`。
- 门禁命令固定为 `pytest -q tests/limiter`，不拆分子集，保持与本地回归口径一致。
- 门禁 job 复用当前分支规则（dev/beta/main，排除 web/schedule），避免扩大本次任务触发面。

## 2026-03-20T08:20:00Z Task: final-wave-f1-remediation
- legacy key 治理统一采用 `build_governed_key/build_governed_key_prefix`，避免各服务自行拼接导致命名漂移。
- 运行时兼容策略选择“治理键优先读取 + 旧裸键回退读取”，并将写入路径全部切到治理键，满足合规同时避免历史数据硬切中断。
- rollout 控制收敛在 `ConcurrencyLimiterService.acquire_with_mode`，通过新增 `rollout_percent` 入参与配置默认值实现集中治理，不改外部 API 形态。

## 2026-03-20T08:50:09Z Task: final-wave-f1-post-remediation-audit
- 审计确认 legacy key 治理已在运行时生效：`ApiKeyService`/`SyncAiModelJob`/`VlmService` 均优先读写治理键（`env:service:legacy:*`），并在读取路径保留裸键回退兼容。
- 审计确认 rollout 为确定性哈希分桶：`ConcurrencyLimiterService._is_user_in_rollout` 使用 `sha256(user_id)` 前8位取模100，`acquire_with_mode` 对 cohort 外用户直接 bypass。
- 结合定向回归（5个指定测试文件共20项）结果，F1 对前次拒绝点已闭合，可给出 APPROVE。

## 2026-03-20T08:52:26Z Task: final-wave-f4-remediation-rerun
- Scope map confirms code changes align to plan tasks: T1(.gitlab-ci.yml/config.example.toml/src/config/config.py/src/base/constants/const.py), T2-T3(src/services/concurrency_limiter.py), T4/T12(src/api_models/api_key/resp_model.py/src/services/api_key.py), T6-T10(src/services/vlm.py/src/routers/vlm.py), T11(src/jobs/ai_model.py), T14(tests/limiter/*.py), T15(.gitlab-ci.yml).
- Final-wave remediation-specific deltas stay within governance + rollout controls: governed legacy key helpers/read-fallback/write-target shifts in api_key/ai_model/vlm + rollout_percent wiring in limiter acquire path; no unrelated domain features observed.
- Verification evidence: pytest -q tests/limiter => 85 passed; diagnostics clean on all changed files except known historical pyright noise in src/services/vlm.py (pre-existing type issues, no new out-of-scope behavior).
