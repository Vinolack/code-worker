# 部署指南

## 运行模式说明

当前镜像为 `registry.gitlab.yeaosound.com/xaio-ai/xaio-code-worker:main`，同一套代码支持 3 种启动方式：

- `python main.py`
  - 兼容模式
  - 启动一个 `scheduler` 子进程
  - 等待 Redis 中的 scheduler 在线信号就绪后，再启动一个单 worker 的 Web 服务
- `python main.py worker`
  - 只启动 Web 服务
  - 使用 `config.toml` 中的 `workers` 配置
- `python main.py scheduler`
  - 只启动 scheduler 服务

## Scheduler 在线信号

scheduler 启动成功后，会在 Redis 中写入一个带 TTL 的在线信号键，用于：

- 标记 scheduler 当前在线
- 作为 docker compose 中 scheduler 容器的健康检查依据
- 在兼容模式下作为 worker 启动前的就绪条件

默认配置位于 `config.toml` / `config.example.toml` 的 `[scheduler]` 段：

```toml
[scheduler]
signal_key = "xaio-code-worker:scheduler:online"
signal_ttl_seconds = 30
signal_refresh_interval_seconds = 10
wait_timeout_seconds = 60
wait_poll_interval_seconds = 1
```

含义如下：

- `signal_key`：Redis 中的在线信号键名
- `signal_ttl_seconds`：在线信号 TTL
- `signal_refresh_interval_seconds`：scheduler 心跳刷新间隔
- `wait_timeout_seconds`：兼容模式下主进程等待 scheduler 就绪的最长时间
- `wait_poll_interval_seconds`：兼容模式下轮询 Redis 的间隔

## 配置准备

所有 compose 示例都默认把本地 `config.toml` 挂载到容器内 `/app/config.toml`。

### 使用内置 Redis 时

如果 compose 里同时启动 Redis，请确保以下配置指向 compose 服务名 `redis`：

```toml
[redis]
host = "redis"
port = 6379
password = ""
db = 2

[limiter.redis]
host = "redis"
port = 6379
password = ""
db = 2
```

### 使用外部 Redis 时

如果 Redis 由外部提供，请把 `[redis]` 和 `[limiter.redis]` 都改成你的外部 Redis 地址。

注意：scheduler 在线信号也写在 `[redis]` 指向的 Redis 中。

## 健康检查脚本

新增脚本：`scripts/check_scheduler_signal.py`

用途：

- compose 中作为 scheduler 容器的 `healthcheck`
- 手动检查 Redis 中是否已写入 scheduler 在线信号

手动执行示例：

```bash
python scripts/check_scheduler_signal.py
```

如果想等待最多 30 秒：

```bash
python scripts/check_scheduler_signal.py --timeout 30 --poll-interval 1
```

## 日志验证脚本

新增脚本：`scripts/verify_scheduler_service.py`

用途：检查日志中 scheduler 的启动/关闭次数是否符合预期。

示例：

```bash
python scripts/verify_scheduler_service.py --log-file logs/server.log --tail-lines 300 --expected-starts 1 --max-shutdowns 0
```

## Compose 示例

### 场景一：传统单服务 + Redis

文件：`docker-compose.single-with-redis.yml`

适用场景：

- 保持向前兼容
- 一个应用容器内部同时跑 scheduler + 单 worker Web
- 一个 Redis 容器

启动命令：

```bash
docker compose -f docker-compose.single-with-redis.yml up -d
```

特点：

- 应用容器使用 `python main.py`
- 主进程会先启动 scheduler 子进程
- 只有等 Redis 中出现 scheduler 在线信号后，才会启动 worker
- compose 层只需要确保 Redis 先健康

### 场景二：worker + scheduler + Redis

文件：`docker-compose.worker-scheduler-with-redis.yml`

适用场景：

- Web 服务和 scheduler 服务拆成两个容器
- Redis 也一起部署在同一个 compose 中

启动命令：

```bash
docker compose -f docker-compose.worker-scheduler-with-redis.yml up -d
```

特点：

- `scheduler` 容器使用 `python main.py scheduler`
- `scheduler` 容器的健康检查依赖 `scripts/check_scheduler_signal.py`
- `worker` 容器依赖：
  - Redis 健康
  - scheduler 健康
- 只有 scheduler 把在线信号写入 Redis 后，worker 才会启动

### 场景三：worker + scheduler + 外部 Redis

文件：`docker-compose.worker-scheduler-external-redis.yml`

适用场景：

- Redis 由外部独立维护
- 当前 compose 只负责 worker 和 scheduler 两个容器

启动命令：

```bash
docker compose -f docker-compose.worker-scheduler-external-redis.yml up -d
```

特点：

- 不包含 Redis 容器
- `scheduler` 的 healthcheck 仍然通过 Redis 在线信号判断
- `worker` 依赖 `scheduler: service_healthy`
- 需要提前保证 `config.toml` 中 Redis 地址可达

## 运维建议

- split 模式下，`scheduler` 默认只部署 1 个实例；否则会重复执行日志上报和模型同步任务
- 显式 `worker` 模式不会主动等待 scheduler；如果你需要启动顺序保证，请通过 compose 的 `depends_on: condition: service_healthy` 控制
- 兼容模式才会在主进程里强制等待 scheduler 在线信号
- 如果 scheduler 能启动但 Redis 不可用，scheduler 在线信号不会成功写入，这时：
  - compat 模式下 worker 不会继续启动
  - split compose 模式下 scheduler 容器不会进入 healthy
- `depends_on: condition: service_healthy` 只控制启动顺序，不会在运行中自动联动重启依赖服务
