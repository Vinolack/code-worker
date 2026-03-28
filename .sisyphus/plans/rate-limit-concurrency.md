# 多实例共享 Redis 的双维度并发限流改造计划

## TL;DR

> **Quick Summary**: 在现有 FastAPI VLM 代理链路中，新增“用户总并发 + 用户-模型并发”双维度分布式限流，基于共享 Redis 和 Lua 原子脚本实现多实例一致性，并覆盖流式断连/超时/异常场景的幂等释放。
>
> **Deliverables**:
> - Redis 分布式并发限流组件（acquire/release/renew）
> - VLM 全请求链路接入（chat/responses/messages/audio/images/tts/proxy）
> - 本期落地静态/本地限额生效，同时预留“服务端动态下发限额”与“user_id 维度限流”扩展位
> - 可回归测试、灰度开关、监控指标与回滚策略
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 3 waves + final verification
> **Critical Path**: Task 1 -> Task 2 -> Task 3 -> Task 6 -> Task 10 -> Task 13 -> Task 14

---

## Context

### Original Request
- 需要深度调研并规划：
  - 用户-单模型最大并发限制
  - 用户总请求最大并发限制
- 项目多机部署，多个实例共享一个 Redis
- 如有必要，需要给出“同步模型/用户 apikey 的后端改造”建议
- 先产出完整 plan，再提供不同部分可选方案

### Interview Summary
**Key Discussions**:
- 限制形态明确为“最大并发数”，不是 QPS/token-bucket
- 部署形态明确为多实例 + 共享 Redis，需要全局一致
- 用户新增约束：长期目标改为“服务端下发每用户总并发 + 每用户每模型并发”，并在鉴权层返回 `user_id` 用于用户维度限流
- 本期边界：不启用上述长期目标，仅完成可回滚的预留改造

**Research Findings**:
- 当前仅图片有本地并发闸门：`src/services/vlm.py:2167`
- 全局 HTTP 连接未限流：`src/utils/http_client.py:124`
- 请求主链路为：`src/middlewares/base.py` -> `src/routers/vlm.py` -> `src/services/vlm.py` -> `src/utils/http_client.py`
- 流式消费者断连后，上游 worker 仍继续执行（统计优先）：`src/utils/http_client.py:459`
- 模型同步启用且原子覆盖：`src/jobs/ai_model.py:111` + `src/jobs/scheduler.py:117`
- API key 全量同步 job 默认未启用：`src/jobs/scheduler.py:87`
- 权限动态查询并缓存：`src/services/api_key.py:329`

### Metis Review
**Identified Gaps** (addressed):
- 明确了 release 必须在上游 worker 终态触发，不能仅依赖下游生成器 finally
- 明确了幂等释放与 owner-token 校验要求
- 明确了灰度模式（off/observe/enforce）和回滚要求
- 明确了仅限“并发限制”范围，避免扩张为通用限流重构

---

## Work Objectives

### Core Objective
在不破坏现有鉴权、模型别名、日志统计链路的前提下，实现可横向扩展的双维度分布式并发限制，保证在多实例部署时限制语义一致、释放可靠、可观测可回滚；同时预留“服务端动态限额 + user_id 维度限流”的无损升级路径（本期不启用）。

### Concrete Deliverables
- 新增分布式并发限流组件（Redis Lua + token 租约）
- 为所有模型调用入口接入限流
- 限额解析策略（本期：模型默认值 + 本地兜底；未来：服务端动态字段可直接接管）
- 限流维度抽象层（本期默认 api_key_digest，预留切换为 user_id）
- 限流拒绝错误契约（HTTP 429 + 统一错误体）
- Redis 键命名空间治理（原有键与限流键统一前缀，避免共享 Redis 冲突）
- 自动化测试与 CI 门禁、运维监控与灰度策略

### Definition of Done
- [x] 双维度限制在并发压测中不被突破（多实例）
- [x] 全实例共享同一 limiter 配额池（同 Redis 目标 + 同命名空间）
- [x] 流式断连/超时/异常后并发槽位可自动或幂等回收
- [x] 限流命中返回 429 且错误体符合现有响应规范
- [x] 原有键与限流键无冲突（前缀治理与类型检查通过）
- [x] 关键测试与 CI 均通过
- [x] 预留开关可验证：切到 `user_id + 服务端限额` 时无需重构主链路

### Must Have
- Redis 原子 acquire/release（Lua）
- request_id token 所有权校验与幂等释放
- 覆盖 chat/responses/messages/audio/images/tts/proxy 全路径
- 共享 Redis 下的全实例同额度生效（所有实例指向同一 limiter Redis 目标与命名空间）
- 原有 Redis 键与限流键统一前缀治理（至少 env + service），避免键冲突
- 灰度开关 + 回滚策略 + 指标告警

### Must NOT Have (Guardrails)
- 不将进程内 semaphore 作为多实例主限流
- 不扩展到 QPS/token-bucket 或权限体系重构
- 不以“客户端断连”作为唯一释放时机
- 不引入破坏现有模型别名解析和权限校验顺序的改造
- 不允许限流键与现有 `user-api-key` / `user-ai-model` 等历史键共用裸 key 名称
- 本期不直接切换到 user_id 生效限流，不依赖后端动态配额作为强制生效来源

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES（pytest）
- **Automated tests**: Tests-after（并发核心可局部 RED->GREEN）
- **Framework**: pytest + pytest-asyncio
- **Reasoning**: 当前测试基线较弱且 CI 无强制测试门禁，先建立确定性限流测试与集成回归更现实

### QA Policy
- **Frontend/UI**: N/A
- **TUI/CLI**: 使用 `interactive_bash`（如需）
- **API/Backend**: 使用 Bash (`curl`) + pytest 执行验证
- **Evidence path**: `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`
- 每个任务必须包含 happy path + failure/edge case 场景

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Start Immediately — foundation + contracts):
- Task 1: 配置与常量层扩展（限流开关、默认阈值、Redis key 前缀）
- Task 2: Redis Lua 原子脚本与键模型（双维度 acquire/release/renew）
- Task 3: 限流服务封装（幂等 token、异常兜底、模式切换）
- Task 4: API 权限响应模型与缓存结构扩展（预留字段：user_id 与动态并发）
- Task 5: 限额解析与主体维度抽象（本期生效：apikey；预留：user_id + 动态限额）

Wave 2 (After Wave 1 — request path integration):
- Task 6: chat/responses/messages 路径接入 pre-submit acquire
- Task 7: audio 路径接入（含文件读取前预检策略）
- Task 8: image 路径接入并修正本地 semaphore 协同时序
- Task 9: tts/embeddings/rerank 及通用 proxy 路径接入
- Task 10: 统一 release 至 worker 终态回调链，修正流式提前释放风险

Wave 3 (After Wave 2 — sync/backends/ops/tests):
- Task 11: `ai_model` 同步链路支持模型默认并发字段（兼容旧 payload）
- Task 12: 后端鉴权接口契约预留（digest-only/perm 字段对齐但默认不启用）
- Task 13: 灰度发布、故障策略、指标与告警落地
- Task 14: 自动化测试（Lua 原子性、幂等释放、流式边界、并发集成）
- Task 15: CI 测试门禁与回归命令固化

Wave FINAL (After ALL tasks — 4 parallel reviews):
- F1: Plan compliance audit (oracle)
- F2: Code quality review (unspecified-high)
- F3: Real manual QA execution (unspecified-high)
- F4: Scope fidelity check (deep)

Critical Path: 1 -> 2 -> 3 -> 6 -> 10 -> 13 -> 14 -> FINAL
Parallel Speedup: ~60%
Max Concurrent: 5

### Dependency Matrix
- **1**: Depends — None | Blocks — 2,3,5,13
- **2**: Depends — 1 | Blocks — 3,6,7,8,9,10,14
- **3**: Depends — 1,2 | Blocks — 6,7,8,9,10
- **4**: Depends — 1 | Blocks — 5,12
- **5**: Depends — 1,4 | Blocks — 6,7,8,9,11,12
- **6**: Depends — 2,3,5 | Blocks — 10,14
- **7**: Depends — 2,3,5 | Blocks — 10,14
- **8**: Depends — 2,3,5 | Blocks — 10,14
- **9**: Depends — 2,3,5 | Blocks — 10,14
- **10**: Depends — 2,3,6,7,8,9 | Blocks — 13,14
- **11**: Depends — 5 | Blocks — 12,14
- **12**: Depends — 4,5,11 | Blocks — 13,14
- **13**: Depends — 1,10,12 | Blocks — 15
- **14**: Depends — 2,6,7,8,9,10,11,12 | Blocks — 15,FINAL
- **15**: Depends — 13,14 | Blocks — FINAL

### Agent Dispatch Summary
- **Wave 1**: T1→quick, T2→deep, T3→deep, T4→quick, T5→quick
- **Wave 2**: T6→unspecified-high, T7→quick, T8→unspecified-high, T9→quick, T10→deep
- **Wave 3**: T11→quick, T12→unspecified-high, T13→deep, T14→unspecified-high, T15→quick
- **FINAL**: F1→oracle, F2→unspecified-high, F3→unspecified-high, F4→deep

---

## TODOs

- [x] 1. 配置与常量层扩展（限流共享 Redis 配置 + 全量键前缀治理）

  **What to do**:
  - 在配置层新增并发限流开关与默认阈值（用户总并发、用户-模型并发、lease TTL、续租间隔、故障策略）。
  - 明确 limiter Redis 目标配置（host/port/db/prefix），保证所有实例连接同一配额池。
  - 在常量层新增统一命名空间前缀规范，覆盖限流键与历史关键键（如 `user-api-key`、`user-ai-model`）的前缀迁移策略。

  **Must NOT do**:
  - 不修改现有业务鉴权逻辑与权限判断行为。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 主要是配置与常量扩展，改动小且边界清晰。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `playwright`: 非前端交互任务，无需浏览器自动化。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2-5)
  - **Blocks**: 2, 3, 5, 13
  - **Blocked By**: None

  **References**:
  - `src/config/config.py:137` - 现有 `VLM_PROXY_*` 配置读取模式，新增 limiter Redis 连接配置应保持一致。
  - `config.example.toml` - 示例配置的组织方式，新增 limiter Redis 与 key 前缀字段需同步模板。
  - `src/base/constants/const.py:16` - 现有历史键定义（`user-api-key`、`user-ai-model`），需纳入前缀治理。
  - `src/jobs/ai_model.py:113` - 历史模型键使用 `rename` 覆盖，迁移前缀时需保证同语义。
  - `src/services/api_key.py:317` - 历史 API key 键使用 `rename` 覆盖，需避免与新限流键冲突。

  **Acceptance Criteria**:
  - [ ] limiter Redis 目标配置可从 `config.toml` 读取并有默认值。
  - [ ] 所有实例在同配置下指向同一 limiter Redis 配额池。
  - [ ] 新增统一 key 前缀常量可被 limiter 与历史关键键迁移逻辑引用。
  - [ ] 前缀治理后，历史键与限流键命名空间不重叠。

  **QA Scenarios**:
  ```
  Scenario: 共享配额池配置 happy path
    Tool: Bash (pytest)
    Preconditions: 两个实例使用同一份 limiter Redis 配置
    Steps:
      1. 运行 pytest -q tests/limiter/test_config_defaults.py::test_shared_limiter_redis_target
      2. 断言 limiter redis host/port/db/prefix 在两个实例配置中一致
      3. 断言 limiter mode/default limits/ttl 均可读且类型正确
    Expected Result: 两实例配置一致并指向同一配额池
    Failure Indicators: 实例间 limiter redis 目标不一致、配置字段缺失或类型错误
    Evidence: .sisyphus/evidence/task-1-config-shared-pool.txt

  Scenario: 历史键冲突检查失败路径
    Tool: Bash (pytest)
    Preconditions: 构造历史裸键（`user-api-key` / `user-ai-model`）与 limiter 裸键同名场景
    Steps:
      1. 运行 pytest -q tests/limiter/test_key_namespace_conflicts.py
      2. 断言冲突检测逻辑命中并返回阻断信号
      3. 断言迁移后键名前缀区分生效
    Expected Result: 冲突可被检测并阻断上线
    Evidence: .sisyphus/evidence/task-1-key-conflict-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-1-config-shared-pool.txt`
  - [ ] `.sisyphus/evidence/task-1-key-conflict-error.txt`

  **Commit**: YES
  - Message: `feat(config): add shared limiter redis target and namespace governance`
  - Files: `src/config/config.py`, `config.example.toml`, `src/base/constants/const.py`
  - Pre-commit: `pytest -q tests/limiter/test_config_defaults.py::test_shared_limiter_redis_target`

- [x] 2. Redis Lua 原子脚本与键模型（双维度 acquire/release/renew）

  **What to do**:
  - 设计并实现 Lua 脚本：同一事务内完成“过期清理 -> 双维度检查 -> 占位写入”。
  - 实现 release/renew 脚本，要求 request_id token 所有权校验与幂等语义。
  - 键名必须统一采用 limiter 命名空间前缀，禁止使用历史裸键格式。

  **Must NOT do**:
  - 不使用多条分散 Redis 命令替代 Lua 原子流程。

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及分布式一致性与并发竞态，需严谨建模。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: 重点是逻辑正确性，不是文档输出。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1,3,4,5)
  - **Blocks**: 3, 6, 7, 8, 9, 10, 14
  - **Blocked By**: 1

  **References**:
  - `src/base/constants/const.py` - 参考已有 key 前缀命名并新增 limiter key 前缀。
  - `src/services/vlm.py:681` - acquire 所需维度（api_key_digest + model_name）来源。
  - `https://redis.io/docs/latest/commands/incr/` - 官方关于原子计数与脚本化建议。
  - `https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/` - token 所有权与安全释放原则。

  **Acceptance Criteria**:
  - [ ] acquire 脚本可同时校验用户总并发和用户-模型并发。
  - [ ] release 脚本重复执行不导致负计数或异常。
  - [ ] renew 脚本仅允许 owner token 续租。
  - [ ] 两个实例并发请求时读写同一组 limiter 命名空间键，额度全局共享。

  **QA Scenarios**:
  ```
  Scenario: 跨实例全局配额 happy path
    Tool: Bash (pytest)
    Preconditions: 启动两个 worker 实例，共享同一 Redis 与 limiter 命名空间
    Steps:
      1. 运行 pytest -q tests/limiter/test_lua_acquire_release.py::test_acquire_dual_dimension_multi_instance
      2. 从两个实例总计并发发起 20 次 acquire（阈值设为 user_total=5, user_model=3）
      3. 断言跨实例总成功数不超过阈值且无超限穿透
    Expected Result: 成功数全局精确受限，无竞态突破
    Failure Indicators: 跨实例成功数超过阈值、脚本报错
    Evidence: .sisyphus/evidence/task-2-lua-global-acquire.txt

  Scenario: 幂等释放失败路径
    Tool: Bash (pytest)
    Preconditions: 已存在一个 request_id 占位
    Steps:
      1. 运行 pytest -q tests/limiter/test_lua_acquire_release.py::test_release_idempotent
      2. 连续执行两次 release 同一 request_id
      3. 断言第二次 release 返回幂等成功且计数不变
    Expected Result: 无负计数，第二次释放不破坏状态
    Evidence: .sisyphus/evidence/task-2-lua-release-idempotent.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-2-lua-global-acquire.txt`
  - [ ] `.sisyphus/evidence/task-2-lua-release-idempotent.txt`

  **Commit**: YES
  - Message: `feat(limiter): add lua scripts for atomic dual-limit control`
  - Files: `src/services/concurrency_limiter.py`, `src/base/constants/const.py`
  - Pre-commit: `pytest -q tests/limiter/test_lua_acquire_release.py`

- [x] 3. 限流服务封装（幂等 token、模式切换、异常兜底）

  **What to do**:
  - 新增并发限流服务封装层，暴露 `acquire/release/renew` 接口，屏蔽 Redis/Lua 细节。
  - 实现 `off/observe/enforce` 三模式与 `fail-open/fail-closed` 策略开关。

  **Must NOT do**:
  - 不把业务异常与 Redis 异常混淆，避免错误码失真。

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 需要把一致性逻辑、安全兜底和可观测模式收敛到统一抽象。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `playwright`: 非 UI 场景。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1,2,4,5)
  - **Blocks**: 6, 7, 8, 9, 10
  - **Blocked By**: 1, 2

  **References**:
  - `src/services/vlm.py:645` - `RequestWrapper` 构造与 submit 前接入位置模板。
  - `src/utils/http_client.py:423` - worker 终态 finally 语义，release 触发应与其对齐。
  - `src/base/exceptions/base.py` - 限流拒绝错误需要保持现有异常体系兼容。

  **Acceptance Criteria**:
  - [ ] observe 模式仅记录 would_block，不拦截请求。
  - [ ] enforce 模式命中限制返回可识别限流错误对象。
  - [ ] fail-open/fail-closed 可配置切换并被测试覆盖。

  **QA Scenarios**:
  ```
  Scenario: observe 模式 happy path
    Tool: Bash (pytest)
    Preconditions: limiter.mode=observe
    Steps:
      1. 运行 pytest -q tests/limiter/test_mode_switch.py::test_observe_mode
      2. 构造超过阈值请求
      3. 断言请求仍放行，同时记录 would_block 指标
    Expected Result: 请求成功 + 指标增加
    Failure Indicators: 请求被错误拦截或无指标记录
    Evidence: .sisyphus/evidence/task-3-observe-mode.txt

  Scenario: fail-closed 失败路径
    Tool: Bash (pytest)
    Preconditions: mock Redis timeout, limiter.fail_policy=closed
    Steps:
      1. 运行 pytest -q tests/limiter/test_mode_switch.py::test_fail_closed_on_redis_error
      2. 触发 acquire 时 Redis 异常
      3. 断言返回限流/服务保护错误，不放行
    Expected Result: 请求被拒绝且错误码可识别
    Evidence: .sisyphus/evidence/task-3-fail-closed-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-3-observe-mode.txt`
  - [ ] `.sisyphus/evidence/task-3-fail-closed-error.txt`

  **Commit**: YES
  - Message: `feat(limiter): add service wrapper and enforcement modes`
  - Files: `src/services/concurrency_limiter.py`, `src/config/config.py`
  - Pre-commit: `pytest -q tests/limiter/test_mode_switch.py`

- [x] 4. API 权限响应模型与缓存结构扩展（预留字段：user_id 与动态并发）

  **What to do**:
  - 扩展 `CodePlanApiKeyDigestOnlyResp` 与 `CodePlanApiKeyPermResp`，新增可选字段：`user_id`、`user_total_concurrency_limit`、`user_model_concurrency_limit`。
  - 在 `ApiKeyService` 缓存读写中保持“字段透传 + 缺省兼容”，确保后端未升级时零影响。

  **Must NOT do**:
  - 不改变本期实际鉴权判定与限流生效口径（仍按 api_key_digest）。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 协议模型与缓存兼容改造，边界清晰。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: 不涉及并发控制算法主体。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1,2,3,5)
  - **Blocks**: 5, 12
  - **Blocked By**: 1

  **References**:
  - `src/api_models/api_key/resp_model.py:19` - digest-only 响应模型。
  - `src/api_models/api_key/resp_model.py:26` - perm 响应模型。
  - `src/services/api_key.py:329` - 权限缓存与远程解析入口。

  **Acceptance Criteria**:
  - [ ] 新增预留字段可解析、可缓存、可回填默认值。
  - [ ] 后端不返回预留字段时不影响现有业务行为。

  **QA Scenarios**:
  ```
  Scenario: 预留字段透传 happy path
    Tool: Bash (pytest)
    Preconditions: mock 后端返回 user_id + limit 字段
    Steps:
      1. 运行 pytest -q tests/limiter/test_api_key_limit_fields.py::test_parse_reserved_fields
      2. 调用 get_code_plan_api_key_perm
      3. 断言字段进入模型并写入缓存
    Expected Result: 解析成功，缓存包含预留字段
    Failure Indicators: pydantic 校验失败或字段丢失
    Evidence: .sisyphus/evidence/task-4-reserved-fields.txt

  Scenario: 兼容旧协议失败路径
    Tool: Bash (pytest)
    Preconditions: mock 后端只返回旧字段
    Steps:
      1. 运行 pytest -q tests/limiter/test_api_key_limit_fields.py::test_backward_compatible_without_reserved
      2. 调用 check_api_key_v3 与 perm 接口
      3. 断言无异常且默认值回退
    Expected Result: 旧协议可运行
    Evidence: .sisyphus/evidence/task-4-backward-compatible.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-4-reserved-fields.txt`
  - [ ] `.sisyphus/evidence/task-4-backward-compatible.txt`

  **Commit**: YES
  - Message: `feat(auth): reserve user_id and dynamic limit fields in auth responses`
  - Files: `src/api_models/api_key/resp_model.py`, `src/services/api_key.py`
  - Pre-commit: `pytest -q tests/limiter/test_api_key_limit_fields.py`

- [x] 5. 限额解析与主体维度抽象（本期生效：apikey；预留：user_id + 动态限额）

  **What to do**:
  - 实现统一的限流主体解析函数（`subject_key`），默认来源 `api_key_digest`，预留 `user_id` 切换能力。
  - 实现统一限额解析函数：本期生效顺序 `模型默认 > 本地兜底`；动态字段仅解析缓存，不参与生效。
  - 将“动态限额生效”与“subject 来源切换”挂到 feature flag，默认关闭。

  **Must NOT do**:
  - 不在本期切换生产生效口径为 user_id。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 抽象层改造为主，逻辑可集中实现。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `ultrabrain`: 不需要复杂非线性推理。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1-4)
  - **Blocks**: 6, 7, 8, 9, 11, 12
  - **Blocked By**: 1, 4

  **References**:
  - `src/middlewares/base.py:138` - 当前可获取 `api_key_digest` 的上下文写入点。
  - `src/services/vlm.py:681` - 模型配置解析点（可拼接 user-model 维度键）。
  - `src/services/api_key.py:394` - 权限查询入口（预留读取 user_id/dynamic limit 字段）。

  **Acceptance Criteria**:
  - [ ] `subject_key` 抽象可在不改调用方的前提下支持未来切换到 user_id。
  - [ ] 默认配置下行为保持本期口径（按 apikey 生效）。
  - [ ] 预留字段存在但开关关闭时，不影响本期限流结果。

  **QA Scenarios**:
  ```
  Scenario: 默认主体口径 happy path
    Tool: Bash (pytest)
    Preconditions: subject_source=apikey（默认）
    Steps:
      1. 运行 pytest -q tests/limiter/test_subject_resolution.py::test_default_subject_is_apikey
      2. 构造请求上下文仅含 api_key_digest
      3. 断言生成 subject_key 成功并可用于限流键
    Expected Result: 行为与当前实现一致
    Failure Indicators: subject 为空或键格式异常
    Evidence: .sisyphus/evidence/task-5-subject-default.txt

  Scenario: 预留字段不开启失败路径
    Tool: Bash (pytest)
    Preconditions: 缓存中含 user_id/dynamic limit 字段，feature flag 关闭
    Steps:
      1. 运行 pytest -q tests/limiter/test_subject_resolution.py::test_reserved_fields_not_effective_when_flag_off
      2. 断言最终限额仍来自模型默认/本地兜底
    Expected Result: 预留字段不影响本期行为
    Evidence: .sisyphus/evidence/task-5-reserved-off.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-5-subject-default.txt`
  - [ ] `.sisyphus/evidence/task-5-reserved-off.txt`

  **Commit**: YES
  - Message: `refactor(limiter): add subject abstraction and reserved dynamic switches`
  - Files: `src/services/vlm.py`, `src/middlewares/base.py`, `src/services/api_key.py`
  - Pre-commit: `pytest -q tests/limiter/test_subject_resolution.py`

- [x] 6. chat/responses/messages 路径接入 pre-submit acquire

  **What to do**:
  - 在 chat/responses/messages 的 `*_do_request` 与 non-stream 请求中，于 `_get_model_config` 后、`proxy_client.submit` 前接入 acquire。
  - acquire 失败时返回统一限流拒绝（429），并记录维度信息（user/model）。

  **Must NOT do**:
  - 不在路由层接入分散限流，保持服务层统一。

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 多路径接入，容易漏点，需要系统性遍历。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: 变更点多，风险高于 quick 任务。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7-10)
  - **Blocks**: 10, 14
  - **Blocked By**: 2, 3, 5

  **References**:
  - `src/services/vlm.py:670` - `stream_chat_do_request`。
  - `src/services/vlm.py:853` - `non_stream_responses`。
  - `src/services/vlm.py:949` - `stream_responses_do_request`。
  - `src/services/vlm.py:1083` - `non_stream_anthropic_messages`。
  - `src/routers/vlm.py:38` - chat/responses/messages 路由入口。

  **Acceptance Criteria**:
  - [ ] chat/responses/messages 所有 submit 前都调用 acquire。
  - [ ] 超限请求返回 429 且包含可追踪维度信息。

  **QA Scenarios**:
  ```
  Scenario: chat 路径限流 happy path
    Tool: Bash (curl)
    Preconditions: 配置 user_total_limit=2, user_model_limit=1；服务运行
    Steps:
      1. 并发发送 2 个不同模型请求到 /v1/chat/completions
      2. 断言两请求都返回 200
      3. 查看 limiter 指标确认 acquire_success=2
    Expected Result: 未超限请求成功
    Failure Indicators: 误拒绝或未记录指标
    Evidence: .sisyphus/evidence/task-6-chat-happy.txt

  Scenario: user-model 超限失败路径
    Tool: Bash (curl)
    Preconditions: 同用户同模型并发阈值=1
    Steps:
      1. 同时发起 2 个 /v1/responses 请求，model 相同
      2. 断言其中 1 个返回 429
      3. 断言错误体包含限流错误码与模型维度
    Expected Result: 精确拦截 1 个请求
    Evidence: .sisyphus/evidence/task-6-model-limit-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-6-chat-happy.txt`
  - [ ] `.sisyphus/evidence/task-6-model-limit-error.txt`

  **Commit**: YES
  - Message: `feat(vlm): enforce limiter in chat responses messages pre-submit`
  - Files: `src/services/vlm.py`, `src/routers/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_vlm_chat_routes_limits.py`

- [x] 7. audio 路径接入限流（含读取文件前预检）

  **What to do**:
  - 在音频转录流式/非流式路径接入 pre-submit acquire。
  - 在路由文件读取前增加“轻量预检（用户总并发）”，避免超限请求先吞大文件内存。

  **Must NOT do**:
  - 不改变现有音频参数和上游协议字段。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 路径明确，主要是接入点和预检顺序调整。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: 无复杂算法，主要是接入正确性。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6,8,9,10)
  - **Blocks**: 10, 14
  - **Blocked By**: 2, 3, 5

  **References**:
  - `src/routers/vlm.py:215` - audio 路由入口与文件读取位置。
  - `src/services/vlm.py:2058` - `audio_transcriptions_do_request` 的 submit 前窗口。
  - `src/services/vlm.py:2124` - 音频流式响应生成器。

  **Acceptance Criteria**:
  - [ ] audio 流式/非流式都在 submit 前 acquire。
  - [ ] 超限时尽可能在读取大文件前拒绝。

  **QA Scenarios**:
  ```
  Scenario: audio happy path
    Tool: Bash (curl)
    Preconditions: 准备 1 个小音频文件，限流阈值允许 1 并发
    Steps:
      1. 调用 /v1/audio/transcriptions (stream=false)
      2. 断言返回 200 且有转录结果
      3. 断言 acquire/release 指标各 +1
    Expected Result: 请求成功且指标闭环
    Failure Indicators: 请求成功但无 release，或直接 429
    Evidence: .sisyphus/evidence/task-7-audio-happy.txt

  Scenario: 预检拒绝失败路径
    Tool: Bash (curl)
    Preconditions: user_total_limit=0 或并发已占满
    Steps:
      1. 调用 /v1/audio/transcriptions 上传大文件
      2. 断言快速返回 429
      3. 断言服务日志无完整文件处理链路
    Expected Result: 快速拒绝，避免高内存开销
    Evidence: .sisyphus/evidence/task-7-audio-precheck-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-7-audio-happy.txt`
  - [ ] `.sisyphus/evidence/task-7-audio-precheck-error.txt`

  **Commit**: YES
  - Message: `feat(audio): add concurrency precheck and limiter acquire`
  - Files: `src/routers/vlm.py`, `src/services/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_audio_limit_paths.py`

- [x] 8. image 路径接入并修正本地 semaphore 协同时序

  **What to do**:
  - 将图片路径改为“分布式限流 + 本地 semaphore（可保留）”协同策略，并明确 acquire/release 顺序。
  - 修复仅依赖 `get_response finally` 的释放时序风险，避免提前释放或遗漏释放。

  **Must NOT do**:
  - 不移除图片路径现有稳定保护能力而无替代机制。

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 该路径已有特殊并发机制，改造容易引入双重释放或泄漏。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: 对并发正确性要求高，不适合轻量处理。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6,7,9,10)
  - **Blocks**: 10, 14
  - **Blocked By**: 2, 3, 5

  **References**:
  - `src/services/vlm.py:2167` - 当前图片本地 semaphore 定义。
  - `src/services/vlm.py:2171` - `image_generations_do_request`。
  - `src/services/vlm.py:2266` - `image_generations_get_response`。

  **Acceptance Criteria**:
  - [ ] 图片路径在多实例下遵循分布式限流阈值。
  - [ ] 本地 semaphore 与分布式 release 无双重释放问题。

  **QA Scenarios**:
  ```
  Scenario: image 双层保护 happy path
    Tool: Bash (curl)
    Preconditions: 启动两个实例共享同一 Redis，user_model_limit=2
    Steps:
      1. 并发发送 3 个 /v1/images/generations 请求
      2. 断言最多 2 个成功，1 个被 429 拒绝
      3. 检查两个实例合计并发不超过 2
    Expected Result: 全局阈值生效
    Failure Indicators: 两实例合计成功 > 2
    Evidence: .sisyphus/evidence/task-8-image-global-limit.txt

  Scenario: 双重释放失败路径
    Tool: Bash (pytest)
    Preconditions: 模拟流式提前断连
    Steps:
      1. 运行 pytest -q tests/limiter/test_image_release_order.py
      2. 断言 release 只执行一次，计数不为负
    Expected Result: 无负值，无重复释放异常
    Evidence: .sisyphus/evidence/task-8-image-release-order-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-8-image-global-limit.txt`
  - [ ] `.sisyphus/evidence/task-8-image-release-order-error.txt`

  **Commit**: YES
  - Message: `fix(image): align local semaphore with distributed limiter lifecycle`
  - Files: `src/services/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_image_release_order.py`

- [x] 9. tts/embeddings/rerank 与通用 proxy 路径接入限流

  **What to do**:
  - 在 TTS、embeddings、rerank 及通用 non-stream proxy 路径接入 acquire。
  - 保证这些路径与 chat 类路径共用同一限流服务封装。

  **Must NOT do**:
  - 不为每个端点重复实现独立限流器。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 接入点分散但模式一致，适合批量改造。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: 不涉及额外算法，仅路径覆盖。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6,7,8,10)
  - **Blocks**: 10, 14
  - **Blocked By**: 2, 3, 5

  **References**:
  - `src/routers/vlm.py:162` - embeddings/rerank 入口。
  - `src/routers/vlm.py:278` - TTS 入口。
  - `src/services/vlm.py` - `proxy_request_non_stream`、`proxy_tts` 相关路径。

  **Acceptance Criteria**:
  - [ ] embeddings/rerank/tts 请求均触发 acquire。
  - [ ] 超限行为与 chat 路径一致（429 + 统一错误体）。

  **QA Scenarios**:
  ```
  Scenario: tts happy path
    Tool: Bash (curl)
    Preconditions: 有效 api_key 与可用 tts 模型
    Steps:
      1. 调用 /audio/v1/tts
      2. 断言返回 200 且内容类型为音频
      3. 断言 limiter 指标记录该请求
    Expected Result: 请求成功，指标完整
    Failure Indicators: 无 acquire 记录或错误拒绝
    Evidence: .sisyphus/evidence/task-9-tts-happy.txt

  Scenario: embeddings 超限失败路径
    Tool: Bash (curl)
    Preconditions: user_total_limit=1，先占满一个并发请求
    Steps:
      1. 并发触发 /v1/embeddings 第二个请求
      2. 断言返回 429
      3. 断言错误体与 chat 路径一致
    Expected Result: 拒绝准确且契约一致
    Evidence: .sisyphus/evidence/task-9-embeddings-limit-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-9-tts-happy.txt`
  - [ ] `.sisyphus/evidence/task-9-embeddings-limit-error.txt`

  **Commit**: YES
  - Message: `feat(vlm): extend limiter coverage to tts embeddings rerank`
  - Files: `src/services/vlm.py`, `src/routers/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_non_chat_paths_limits.py`

- [x] 10. 统一 release 到 worker 终态回调链（修复流式提前释放风险）

  **What to do**:
  - 将 release 触发点统一收敛到上游 worker 成功/失败/取消终态回调后。
  - 处理 submit 失败、上游失败、客户端断连、超时等路径的 exactly-once 释放语义。

  **Must NOT do**:
  - 不再依赖单一路径（例如 generator finally）作为唯一 release 机制。

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及生命周期竞态与幂等保障，是一致性关键点。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: 容错复杂度较高，不适合轻量执行。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6-9)
  - **Blocks**: 13, 14
  - **Blocked By**: 2, 3, 6, 7, 8, 9

  **References**:
  - `src/utils/http_client.py:337` - on_success 回调触发点。
  - `src/utils/http_client.py:421` - on_failure 回调触发点。
  - `src/utils/http_client.py:423` - worker finally 终态。
  - `src/utils/http_client.py:459` - 消费者断连但 worker 继续执行语义。
  - `src/services/vlm.py:2266` - 当前图片流式 get_response finally 释放点（风险样例）。

  **Acceptance Criteria**:
  - [ ] release 在成功/失败/取消路径均可触发且幂等。
  - [ ] 客户端断连不会导致提前释放并发槽位。

  **QA Scenarios**:
  ```
  Scenario: 流式正常结束 happy path
    Tool: Bash (pytest)
    Preconditions: 构造 stream 请求并启用 limiter
    Steps:
      1. 运行 pytest -q tests/limiter/test_stream_release_semantics.py::test_release_on_worker_terminal_success
      2. 完整消费流式响应
      3. 断言 release 在 worker 终态后执行
    Expected Result: release 时序正确且仅一次
    Failure Indicators: 提前释放或重复释放
    Evidence: .sisyphus/evidence/task-10-stream-release-happy.txt

  Scenario: 客户端断连失败路径
    Tool: Bash (pytest)
    Preconditions: 流式请求中途主动断开消费者
    Steps:
      1. 运行 pytest -q tests/limiter/test_stream_release_semantics.py::test_disconnect_not_early_release
      2. 断言 worker 继续完成
      3. 断言 release 发生在 worker 完成后
    Expected Result: 无提前释放，无泄漏
    Evidence: .sisyphus/evidence/task-10-stream-disconnect-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-10-stream-release-happy.txt`
  - [ ] `.sisyphus/evidence/task-10-stream-disconnect-error.txt`

  **Commit**: YES
  - Message: `fix(limiter): release permits on worker terminal states only`
  - Files: `src/utils/http_client.py`, `src/services/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_stream_release_semantics.py`

- [x] 11. ai_model 同步链路支持模型默认并发字段（兼容旧 payload）

  **What to do**:
  - 在 `SyncAiModelJob` 中保留/透传模型默认并发字段（例如 model_default_concurrency）。
  - 保证 alias 与 lowercase 副本也携带该字段，保持读取一致。

  **Must NOT do**:
  - 不改变现有加解密字段行为与展示筛选规则。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 变更集中在同步解析和 Redis 映射层。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: 不涉及复杂并发执行路径。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 12-15)
  - **Blocks**: 12, 14
  - **Blocked By**: 5

  **References**:
  - `src/jobs/ai_model.py:71` - 模型字段写入 hash 映射位置。
  - `src/jobs/ai_model.py:92` - alias 复制逻辑。
  - `src/jobs/ai_model.py:105` - lowercase 复制逻辑。

  **Acceptance Criteria**:
  - [ ] 新增默认并发字段在主模型/alias/lowercase 均可读。
  - [ ] 旧 payload 无该字段时同步流程不报错。

  **QA Scenarios**:
  ```
  Scenario: 同步透传 happy path
    Tool: Bash (pytest)
    Preconditions: mock ai_model payload 含 model_default_concurrency
    Steps:
      1. 运行 pytest -q tests/limiter/test_ai_model_sync_limit_fields.py::test_sync_persists_limit_fields
      2. 读取 Redis hash 主键与 alias 键
      3. 断言字段一致存在
    Expected Result: 三种键都携带默认并发字段
    Failure Indicators: alias/lowercase 丢字段
    Evidence: .sisyphus/evidence/task-11-ai-model-sync-happy.txt

  Scenario: 旧 payload 回退路径
    Tool: Bash (pytest)
    Preconditions: mock 不含并发字段 payload
    Steps:
      1. 运行 pytest -q tests/limiter/test_ai_model_sync_limit_fields.py::test_sync_without_limit_fields
      2. 断言同步成功且读取端回退默认值
    Expected Result: 同步不失败
    Evidence: .sisyphus/evidence/task-11-ai-model-sync-fallback.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-11-ai-model-sync-happy.txt`
  - [ ] `.sisyphus/evidence/task-11-ai-model-sync-fallback.txt`

  **Commit**: YES
  - Message: `feat(sync): persist model default concurrency fields in ai model sync`
  - Files: `src/jobs/ai_model.py`, `src/services/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_ai_model_sync_limit_fields.py`

- [x] 12. 后端鉴权接口契约预留（digest-only/perm 字段对齐但默认不启用）

  **What to do**:
  - 与后端约定字段契约：`user_id`、`user_total_concurrency_limit`、`user_model_concurrency_limit`。
  - worker 侧完成字段解析与缓存预留，但默认不作为本期生效限额来源。
  - 增加 feature flag 预留：后续开启后可切到“服务端下发限额 + user_id 维度限流”。

  **Must NOT do**:
  - 不在本期强依赖后端字段并改变当前限流生效逻辑。

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 涉及跨服务契约与兼容发布策略。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: 跨系统约束较多。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 11,13,14,15)
  - **Blocks**: 13, 14
  - **Blocked By**: 4, 5, 11

  **References**:
  - `src/services/api_key.py:141` - digest-only 远程接口调用点。
  - `src/services/api_key.py:374` - perm 远程接口调用点。
  - `src/api_models/api_key/resp_model.py` - 响应模型承载字段。
  - `src/jobs/scheduler.py:87` - API key 全量同步当前状态（注释），用于说明本期不依赖全量同步承载动态字段。

  **Acceptance Criteria**:
  - [ ] 后端返回预留字段时 worker 可解析并缓存。
  - [ ] 默认配置下这些字段不影响本期限流行为。
  - [ ] 开启未来 feature flag 后具备无破坏切换条件（通过测试证明）。

  **QA Scenarios**:
  ```
  Scenario: 契约预留 happy path
    Tool: Bash (pytest)
    Preconditions: mock 后端返回 user_id + dynamic limits
    Steps:
      1. 运行 pytest -q tests/limiter/test_backend_contract_limits.py::test_reserved_contract_cached
      2. 发起请求并读取缓存
      3. 断言字段已缓存但未改变本期生效阈值
    Expected Result: 预留字段可用且行为不变
    Failure Indicators: 行为被意外切换到动态限额
    Evidence: .sisyphus/evidence/task-12-contract-reserved.txt

  Scenario: 后端旧版本兼容路径
    Tool: Bash (pytest)
    Preconditions: mock 后端不返回新字段
    Steps:
      1. 运行 pytest -q tests/limiter/test_backend_contract_limits.py::test_old_contract_fallback
      2. 发起请求
      3. 断言系统无异常并沿用本期限额策略
    Expected Result: 服务连续性保持
    Evidence: .sisyphus/evidence/task-12-contract-fallback.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-12-contract-reserved.txt`
  - [ ] `.sisyphus/evidence/task-12-contract-fallback.txt`

  **Commit**: YES
  - Message: `feat(auth-contract): reserve user-id and dynamic-limit fields without enforcement`
  - Files: `src/services/api_key.py`, `src/api_models/api_key/resp_model.py`, `src/config/config.py`
  - Pre-commit: `pytest -q tests/limiter/test_backend_contract_limits.py`

- [x] 13. 灰度发布、故障策略、指标与告警落地

  **What to do**:
  - 实现限流运行模式：`off/observe/enforce`，并支持按用户哈希灰度比例。
  - 实现故障策略开关（Redis 异常 fail-open/fail-closed）和关键指标埋点。
  - 输出告警阈值与回滚触发条件（指标驱动）。

  **Must NOT do**:
  - 不在未观测指标前直接全量 enforce。

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: 涉及发布安全性与稳定性治理。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `quick`: 需要系统级策略编排。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 11,12,14,15)
  - **Blocks**: 15
  - **Blocked By**: 1, 10, 12

  **References**:
  - `src/config/config.py` - 新增模式与策略配置入口。
  - `src/services/vlm.py` - 记录限流命中维度日志的最佳位置。
  - `src/utils/http_client.py:423` - 释放生命周期指标采样点。

  **Acceptance Criteria**:
  - [ ] 支持 `off/observe/enforce` 热切换。
  - [ ] 提供 blocked/acquire/release/redis_error 等核心指标。
  - [ ] 有明确回滚触发规则（例如 blocked rate、5xx 激增）。

  **QA Scenarios**:
  ```
  Scenario: observe 灰度 happy path
    Tool: Bash (curl)
    Preconditions: mode=observe, 灰度比例 20%
    Steps:
      1. 用 2 个不同用户发请求（一个命中灰度，一个不命中）
      2. 断言都返回业务成功
      3. 断言仅灰度用户产生 would_block 指标
    Expected Result: 灰度范围与行为正确
    Failure Indicators: 非灰度用户被错误处理
    Evidence: .sisyphus/evidence/task-13-observe-rollout.txt

  Scenario: Redis 异常策略失败路径
    Tool: Bash (pytest)
    Preconditions: 模拟 Redis 超时，分别设置 fail-open 与 fail-closed
    Steps:
      1. 运行 pytest -q tests/limiter/test_redis_failure_policy.py
      2. 断言 fail-open 放行且记错误指标
      3. 断言 fail-closed 拒绝且返回保护错误
    Expected Result: 两种策略行为符合配置
    Evidence: .sisyphus/evidence/task-13-redis-failure-policy.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-13-observe-rollout.txt`
  - [ ] `.sisyphus/evidence/task-13-redis-failure-policy.txt`

  **Commit**: YES
  - Message: `feat(ops): add rollout modes metrics and failure policy controls`
  - Files: `src/config/config.py`, `src/services/concurrency_limiter.py`, `src/services/vlm.py`
  - Pre-commit: `pytest -q tests/limiter/test_redis_failure_policy.py`

- [x] 14. 自动化测试（Lua 原子性、幂等释放、流式边界、并发集成）

  **What to do**:
  - 建立 `tests/limiter/` 测试集，覆盖原子性、幂等、流式断连、超时、并发压测。
  - 增加多实例模拟（共享 Redis）集成测试，验证双维度阈值不穿透。

  **Must NOT do**:
  - 不依赖真实外网模型接口做限流正确性判断。

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: 需要大量可重复的并发测试与边界场景验证。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `writing`: 重点是自动化验证，不是文档生产。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 11,12,13,15)
  - **Blocks**: 15, FINAL
  - **Blocked By**: 2, 6, 7, 8, 9, 10, 11, 12

  **References**:
  - `requirements.txt:18` - pytest/pytest-asyncio 已存在，可直接复用。
  - `tests/test_chat.py` - 现有测试风格不足，需引入断言驱动测试模式。
  - `src/utils/http_client.py` - 流式与断连行为测试目标。

  **Acceptance Criteria**:
  - [ ] 限流核心测试文件全部通过。
  - [ ] 至少覆盖 happy path + failure path + race path。

  **QA Scenarios**:
  ```
  Scenario: 并发集成 happy path
    Tool: Bash (pytest)
    Preconditions: 测试 Redis 可用
    Steps:
      1. 运行 pytest -q tests/limiter/test_concurrency_integration.py::test_dual_dimension_limits
      2. 并发发起多请求跨模型/同模型混合流量
      3. 断言 user_total 和 user_model 都不突破阈值
    Expected Result: 双维度限制同时生效
    Failure Indicators: 任一维度被突破
    Evidence: .sisyphus/evidence/task-14-concurrency-integration.txt

  Scenario: 超时与崩溃恢复失败路径
    Tool: Bash (pytest)
    Preconditions: 模拟持有租约后进程异常终止
    Steps:
      1. 运行 pytest -q tests/limiter/test_lease_recovery.py::test_ttl_recovers_leaked_slots
      2. 等待 TTL 到期
      3. 断言租约自动恢复并可再次 acquire
    Expected Result: 无人工干预恢复
    Evidence: .sisyphus/evidence/task-14-lease-recovery-error.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-14-concurrency-integration.txt`
  - [ ] `.sisyphus/evidence/task-14-lease-recovery-error.txt`

  **Commit**: YES
  - Message: `test(limiter): add deterministic unit and integration suites`
  - Files: `tests/limiter/test_lua_acquire_release.py`, `tests/limiter/test_stream_release_semantics.py`, `tests/limiter/test_concurrency_integration.py`
  - Pre-commit: `pytest -q tests/limiter`

- [x] 15. CI 测试门禁与回归命令固化

  **What to do**:
  - 在 CI 增加 test stage 或 test job，执行 `pytest -q tests/limiter` 与核心回归集。
  - 更新本地执行命令和失败排查说明，确保限流回归可持续运行。

  **Must NOT do**:
  - 不让限流改造在 CI 中处于“无测试门禁”状态。

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 配置与命令化改造，复杂度可控。
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**:
    - `deep`: 无复杂算法。

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 11-14)
  - **Blocks**: FINAL
  - **Blocked By**: 13, 14

  **References**:
  - `.gitlab-ci.yml:1` - 当前 stages 无 test，需要新增。
  - `requirements.txt` - 已具备 pytest 依赖，无需新增测试框架。

  **Acceptance Criteria**:
  - [ ] CI 中存在并发限流测试 job。
  - [ ] MR/分支流水线可见限流测试通过/失败状态。

  **QA Scenarios**:
  ```
  Scenario: CI 测试 job happy path
    Tool: Bash
    Preconditions: 本地可执行 CI 等价命令
    Steps:
      1. 运行 pytest -q tests/limiter
      2. 运行与 CI 同步的回归命令集合
      3. 断言全部通过
    Expected Result: 命令可稳定通过
    Failure Indicators: CI 配置与本地命令不一致
    Evidence: .sisyphus/evidence/task-15-ci-happy.txt

  Scenario: 失败即阻断路径
    Tool: Bash
    Preconditions: 人为引入一个失败断言（测试分支）
    Steps:
      1. 运行 pytest -q tests/limiter
      2. 断言返回非 0
      3. 验证 CI job 将标记失败
    Expected Result: 测试失败时流水线阻断
    Evidence: .sisyphus/evidence/task-15-ci-failure-gate.txt
  ```

  **Evidence to Capture:**
  - [ ] `.sisyphus/evidence/task-15-ci-happy.txt`
  - [ ] `.sisyphus/evidence/task-15-ci-failure-gate.txt`

  **Commit**: YES
  - Message: `ci(test): enforce limiter regression gates in pipeline`
  - Files: `.gitlab-ci.yml`
  - Pre-commit: `pytest -q tests/limiter`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

- [x] F1. **Plan Compliance Audit** — `oracle`
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [x] F3. **Real QA Execution** — `unspecified-high`
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `feat(config): add distributed concurrency limiter defaults and flags`
- **2**: `feat(limiter): add redis lua acquire release renew primitives`
- **3**: `feat(vlm): integrate two-level concurrency limiter across request paths`
- **4**: `feat(sync): reserve dynamic concurrency contract and keep model-default enforcement`
- **5**: `test(limiter): add deterministic concurrency and stream release tests`
- **6**: `ci(test): enforce pytest gate for limiter regression`

---

## Success Criteria

### Verification Commands
```bash
pytest -q tests
```

```bash
pytest -q tests/limiter
```

```bash
python main.py
```

### Final Checklist
- [x] 双维度并发限制在多实例下生效
- [x] 所有实例共享同一 limiter Redis 配额池（目标与命名空间一致）
- [x] 所有流式/非流式路径都有 acquire/release
- [x] release 幂等且无负计数/泄漏
- [x] 原有键与限流键完成前缀隔离且无冲突
- [x] 灰度、监控、回滚链路可执行
- [x] 测试与 CI 全部通过
