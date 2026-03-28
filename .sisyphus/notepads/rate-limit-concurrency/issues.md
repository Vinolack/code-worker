# Issues

## 2026-03-20T03:58:17Z Task: init
- Awaiting issue discoveries.

## 2026-03-20T04:11:00Z Task: task-1-research
- Baseline repository has many pre-existing LSP/type issues unrelated to this plan; verification must focus on regression and targeted tests, not full clean-type baseline.
- Potential migration risk: legacy key rename/delete operations can collide with new namespace rollout if read/write compatibility is not staged.

## 2026-03-20T00:00:00Z Task: task-1-implementation
- Required verification command `pytest -q tests/limiter/test_config_defaults.py::test_shared_limiter_redis_target` cannot run in current workspace because `tests/limiter/test_config_defaults.py` does not exist yet.
- LSP diagnostics are unavailable for TOML in this repo (`.toml` server not configured), so `config.example.toml` was validated via syntax-safe minimal edits only.

## 2026-03-20T04:20:57Z Task: task-1-verification-fix
- No new blocker found; required selector test now exists and passes locally.

## 2026-03-20T04:21:12Z Task: task-1-verification-fix
- No new blocker found; required selector test now exists and passes locally.

## 2026-03-20T04:30:07Z Task: task-1-namespace-collision-tests
- Initial run failed with `ModuleNotFoundError: No module named src`; resolved by loading `src/base/constants/const.py` via file-path import in test to avoid PYTHONPATH dependency.

## 2026-03-20T00:00:00Z Task: task-2-lua-atomic-limiter
- 目标测试初次执行出现`ModuleNotFoundError: No module named 'src'`；通过在测试文件中注入项目根路径到`sys.path`解决。

## 2026-03-20T04:48:50Z Task: task-3-mode-wrapper
- 本仓库存在大量既有LSP噪音（非本任务改动），因此仅对改动文件执行了定向`lsp_diagnostics`并保持清洁。

## 2026-03-20T00:00:00Z Task: task-4-api-auth-model-cache-reservation
- `src/services/api_key.py`存在既有类型标注缺口（如Redis JSON方法返回签名），本次在改动范围内做了最小类型修复以保持定向LSP clean。

-  存在大量既有 Pyright 报错（与本次改动无关），导致 changed file 无法达到完全 clean。

- Task5补充: src/services/vlm.py存在大量既有Pyright报错（非本次新增），changed file无法达到全量clean。

## 2026-03-20T05:42:34Z Task: task-6-pre-submit-acquire
- `src/services/vlm.py` 存在既有 Pyright 噪音（Redis 客户端 await/type 标注相关），本任务仅做 changed-file 定向检查并确认未新增阻断性语法问题。

## 2026-03-20T06:02:13Z Task: task-7-audio-limit-paths
-  仍有大量既有Pyright噪音（与本任务无关）；本次定向检查确认与新测试文件无新增类型错误。

## 2026-03-20T06:02:43Z Task: task-7-audio-limit-paths-fix
- src/services/vlm.py存在既有Pyright噪音，本任务未新增src/routers/vlm.py和tests/limiter/test_audio_limit_paths.py的类型错误。

## 2026-03-20T06:19:22Z Task: task-8-image-limit-release-order
- `src/services/vlm.py` 仍有既有 Pyright 噪音（历史遗留）；本次改动区域无新增 diagnostics，新增测试文件类型检查为 clean。


## 2026-03-20T06:31:31Z Task: task-9-non-chat-limit-paths
- `src/services/vlm.py` 仍存在历史 Pyright 噪音（任务上下文已标注），本次仅保证新增测试文件无 diagnostics 且改动区域未引入阻断性回归。

## 2026-03-20T06:55:00Z Task: task-10-stream-release-semantics
- `src/services/vlm.py` 历史 Pyright 噪音仍未清零，本任务新增回调/兜底逻辑无法在全文件维度达成 clean，只能通过定向测试和新增测试文件 clean 控制回归风险。
- `tests/limiter/test_stream_release_semantics.py` 的 upstream failure 场景会触发 `Future exception was never retrieved` 日志噪音（测试通过但有 stderr 提示），属于 AsyncHttpClient 现有行为。

## 2026-03-20T07:06:14Z Task: task-11-ai-model-sync-default-concurrency
- 本任务无新增阻断问题；定向 `lsp_diagnostics` 与新增测试文件均为 clean。

## 2026-03-20T07:18:13Z Task: task-12-auth-contract-reservation
- 新测试初版对 `service.client` 直接赋值触发 Pyright 类型报错；已改为 `monkeypatch.setattr(..., raising=False)` 以保持 changed-file diagnostics clean。

## 2026-03-20T07:29:13Z Task: task-14-automated-tests
- 仓库仍有既有 `src/services/vlm.py` 全文件类型噪音；本任务只能保证新增/改动测试文件定向 diagnostics clean。
- `tests/limiter/test_stream_release_semantics.py` upstream failure 场景的 stderr 噪音仍是已知基线（非新增回归）。

## 2026-03-20T07:34:08Z Task: task-13-runtime-policy-metrics
- `src/services/vlm.py` 仍存在历史 Pyright 全文件错误，无法在本任务范围内实现文件级 diagnostics clean；本次改动仅保证 `src/config/config.py`、`src/services/concurrency_limiter.py` 与新增测试文件 clean。

## 2026-03-20T07:41:25Z Task: task-15-ci-limiter-gate
- 本地 `pytest -q tests/limiter` 仍有 `starlette.formparsers` 的 PendingDeprecationWarning 噪音；不影响回归门禁判定（测试全部通过）。

## 2026-03-20T08:20:00Z Task: final-wave-f1-remediation
- `src/services/vlm.py` 仍有大量既有 Pyright 诊断噪音（历史基线），本次仅确保新增治理键读取辅助方法未引入新的 awaitable 类型报错。
- 工作区当前存在与本任务无关的预置改动与未跟踪文件（如`.gitlab-ci.yml`、`src/routers/vlm.py`、`tests/limiter/`目录），提交前需按任务边界筛选。

## 2026-03-20T08:50:09Z Task: final-wave-f1-post-remediation-audit
- 未发现新的阻断问题；已验证历史 bare key 兼容回退与 rollout 分桶旁路行为符合计划要求。

## 2026-03-20T08:56:13Z Task: final-wave-f2-rerun-post-remediation
- Build command `python -m compileall src` passed; no syntax/compile regression found in limiter remediation scope.
- LSP diagnostics on changed Python files are clean except `src/services/vlm.py`, which still shows historical Pyright noise (unbound-variable and Optional[str] assignment reports) already known as baseline.
- Full suite `pytest -q tests` shows `86 passed, 2 failed` with failures isolated to `tests/test_chat.py` external connect timeout (`openai.APITimeoutError`), consistent with inherited baseline and not limiter-specific.
- Limiter gate suite `pytest -q tests/limiter` is green (`85 passed`), no new limiter regression observed.
