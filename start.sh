#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
# 优先使用项目 .venv,避免污染全局环境;允许通过 PYTHON_BIN 覆盖
if [ -z "${PYTHON_BIN:-}" ] && [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi
BACKEND_PID=""
WORKER_PID=""
FRONTEND_PID=""
STOPPED=0

info() {
  printf '\033[1;34m%s\033[0m\n' "$1"
}

error() {
  printf '\033[1;31m%s\033[0m\n' "$1" >&2
}

cleanup() {
  if [ "$STOPPED" -eq 1 ]; then
    return
  fi
  STOPPED=1
  info "正在关闭前后端..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && wait "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$WORKER_PID" ] && wait "$WORKER_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && wait "$BACKEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  error "未找到 Python。请安装 Python 3.10 或更高版本。"
  exit 1
}

command -v npm >/dev/null 2>&1 || {
  error "未找到 npm。请安装 Node.js 18 或更高版本。"
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
  "$PYTHON_BIN" -c 'import app, langgraph'
) >/dev/null 2>&1; then
  info "正在安装后端依赖..."
  (
    cd "$BACKEND_DIR"
    "$PYTHON_BIN" -m pip install --require-hashes -r requirements-dev.txt
  )
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  info "正在安装前端依赖..."
  (
    cd "$FRONTEND_DIR"
    npm ci
  )
fi

info "正在检查数据库并执行迁移..."
if ! (
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -m alembic upgrade head
); then
  error "数据库连接或迁移失败，请检查 backend/.env 中的 DATABASE_URL。"
  exit 1
fi

info "正在启动后端：http://localhost:8000"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
) &
BACKEND_PID=$!

info "正在启动持久化任务 worker"
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m app.worker
) &
WORKER_PID=$!

info "正在启动前端：http://localhost:5173"
(
  cd "$FRONTEND_DIR"
  exec npm run dev
) &
FRONTEND_PID=$!

printf '\n\033[1;32mAI Marking 已启动，按 Ctrl+C 同时关闭前后端。\033[0m\n\n'

while kill -0 "$BACKEND_PID" 2>/dev/null \
  && kill -0 "$WORKER_PID" 2>/dev/null \
  && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

error "前端或后端进程已退出。"
exit 1
