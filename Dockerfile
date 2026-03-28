ARG WHEELS_IMAGE=deps_local
FROM python:3.12-slim-trixie AS deps_local

WORKDIR /app
RUN sed -i 's|http://deb.debian.org|http://mirrors.xtom.hk|g' /etc/apt/sources.list.d/debian.sources
# 安装编译依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    curl && \
    rm -rf /var/lib/apt/lists/*

# 升级 pip
RUN pip install --no-cache-dir --upgrade pip

# 复制 requirements.txt 并构建 wheels
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# Stage 2: Wheels Source Selector
# This stage selects either the locally built 'deps_local' or the external image passed via --build-arg
FROM ${WHEELS_IMAGE} AS wheels_source

# Stage 3: Runtime
FROM python:3.12-slim-trixie

WORKDIR /app
# 安装运行时依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone

# 从 wheels_source 阶段复制编译好的 wheels 并安装
COPY --from=wheels_source /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && \
    rm -rf /wheels

# 复制应用代码
COPY . .

EXPOSE 8084

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8084/status/ping || exit 1

CMD ["python", "main.py"]
