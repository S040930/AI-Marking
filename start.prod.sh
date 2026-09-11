#!/usr/bin/env bash
# 本机优化启动脚本:单 API 进程 + 单任务 worker + 前端生产构建预览。
# 使用方式:bash start.prod.sh
# 为保证进程内资源与配置语义一致，WORKERS 只允许为 1。

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
if [ -z "${PYTHON_BIN:-}" ] && [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi
BACKEND_PID=""
WORKER_PID=""
ACP_WORKER_PID=""
FRONTEND_PID=""
STOPPED=0
WORKERS="${WORKERS:-1}"

info() {
  printf '\033[1;34m%s\033[0m\n' "$1"
}

error() {
  printf '\033[1;31m%s\033[0m\n' "$1" >&2
}

if [ "$WORKERS" != "1" ]; then
  error "本机优化模式仅允许 WORKERS=1；请使用默认值或显式设置为 1。"
  exit 1
fi

backend_port_is_open() {
  "$PYTHON_BIN" - <<'PY'
import socket

with socket.socket() as sock:
    sock.settimeout(0.3)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", 8000)) == 0 else 1)
PY
}

backend_service_identity() {
  "$PYTHON_BIN" - <<'PY'
import json
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1) as response:
        payload = json.load(response)
except Exception:
    print("unknown")
else:
    print(payload.get("service", "unknown"))
PY
}

ensure_backend_port_available() {
  if ! backend_port_is_open; then
    return
  fi
  if [ "$(backend_service_identity)" = "ai-marking" ]; then
    error "8000 端口已有 AI-Marking 进程。请先停止旧进程，避免 MCP 连接到过期后端。"
  else
    error "8000 端口已被其他服务占用。请停止该进程后再启动 AI-Marking。"
  fi
  exit 1
}

wait_for_backend() {
  local attempt identity
  for attempt in $(seq 1 20); do
    if kill -0 "$BACKEND_PID" 2>/dev/null && backend_port_is_open; then
      identity="$(backend_service_identity)"
      if [ "$identity" = "ai-marking" ]; then
        info "后端身份检查通过：ai-marking API"
        return
      fi
    fi
    sleep 1
  done
  error "后端未在 20 秒内通过服务身份检查，请查看上方 uvicorn 日志。"
  exit 1
}

cleanup() {
  if [ "$STOPPED" -eq 1 ]; then
    return
  fi
  STOPPED=1
  info "正在关闭前后端..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null || true
  [ -n "$ACP_WORKER_PID" ] && kill "$ACP_WORKER_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && wait "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$WORKER_PID" ] && wait "$WORKER_PID" 2>/dev/null || true
  [ -n "$ACP_WORKER_PID" ] && wait "$ACP_WORKER_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && wait "$BACKEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  error "未找到 Python。请安装 Python 3.10 或更高版本。"
  exit 1
}

command -v npm >/dev/null 2>&1 || {
  error "未找到 npm。请安装 .nvmrc 指定的 Node.js 26.4.0。"
  exit 1
}

"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  error "Python 版本过低，请使用 Python 3.10 或更高版本。"
  exit 1
}

if [ ! -f "$BACKEND_DIR/.env" ]; then
  error "缺少 backend/.env。请先复制 backend/.env.example 并配置数据库。"
  exit 1
fi

if ! (
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -c 'import app, mcp, acp'
) >/dev/null 2>&1; then
  info "生产依赖未就绪，运行 bootstrap --prod..."
  "$ROOT_DIR/scripts/bootstrap" --prod
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  info "正在安装前端依赖..."
  (
    cd "$FRONTEND_DIR"
    npm ci
  )
fi

ensure_backend_port_available

info "正在检查数据库并执行迁移..."
if ! (
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -m alembic upgrade head
); then
  error "数据库连接或迁移失败，请检查 backend/.env 中的 DATABASE_URL。"
  exit 1
fi

info "正在构建前端生产包..."
(
  cd "$FRONTEND_DIR"
  npm run build
)

info "WORKERS=$WORKERS"

info "正在启动后端(生产模式,$WORKERS workers):http://localhost:8000"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers "$WORKERS"
) &
BACKEND_PID=$!
wait_for_backend

info "正在启动持久化任务 worker(并发 ${TASK_CONCURRENCY:-2})"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m app.worker
) &
WORKER_PID=$!

info "正在启动 ACP 批改 worker(并发 1)"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m app.acp_worker
) &
ACP_WORKER_PID=$!

info "正在启动前端预览:http://localhost:5173"
(
  cd "$FRONTEND_DIR"
  exec npm run preview -- --host 127.0.0.1 --port 5173
) &
FRONTEND_PID=$!

printf '\n\033[1;32mAI Marking 已启动（生产模式），按 Ctrl+C 同时关闭前后端。\033[0m\n\n'

while kill -0 "$BACKEND_PID" 2>/dev/null \
  && kill -0 "$WORKER_PID" 2>/dev/null \
  && kill -0 "$ACP_WORKER_PID" 2>/dev/null \
  && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

error "前端或后端进程已退出。"
exit 1
