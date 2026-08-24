#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
PID_FILE="$SCRIPT_DIR/.fusion-project-svc.pid"
LOG_DIR="$SCRIPT_DIR/logs"
# boot.log captures only pre-handler startup output (before the in-process
# RotatingFileHandler attaches to stdout.log). The daemon owns stdout.log
# rotation in-process (config.LOG_MAX_BYTES / LOG_BACKUP_COUNT); pointing the
# shell redirect at the same file would collide on rotation (inode divergence).
BOOT_LOG="$LOG_DIR/boot.log"
ENTRY="python3 -m project_service.daemon_server"
SOCK_PATH="${FUSION_PROJECT_SOCK:-/tmp/fusion-project-svc.sock}"

# 上游 fusion-agent-studio 的 HTTP api_server 端口（issue#100 修复后 daemon 在 11455 起 FastAPI）。
# 默认与 agent-studio 对齐；如需覆盖导出 FUSION_AGENT_STUDIO_URL 即可。
export FUSION_AGENT_STUDIO_URL="${FUSION_AGENT_STUDIO_URL:-http://127.0.0.1:11455}"

mkdir -p "$LOG_DIR"

# process-identity check: a live PID is ours only if its cmdline still
# references the daemon entry. prevents stale-PID reuse → false "already running".
_pid_is_daemon() {
    local pid="$1"
    local cmd
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [ -n "$cmd" ] || return 1
    case "$cmd" in
        *project_service.daemon_server*) return 0 ;;
        *) return 1 ;;
    esac
}

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    _pid_is_daemon "$pid"
}

do_start() {
    if is_running; then
        echo "fusion-project-svc already running (pid $(cat "$PID_FILE"))"
        return 0
    fi
    # stale PID file left by a crashed/killed instance: clear it so we don't
    # mistake a reused PID for our daemon.
    rm -f "$PID_FILE"
    if [ -d "$VENV_DIR" ]; then
        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"
    fi
    # mkdir-based atomic lock guards against two start invocations racing to
    # spawn a daemon (portable: no flock dependency, works on macOS + Linux).
    if ! mkdir "$PID_FILE.lock" 2>/dev/null; then
        echo "fusion-project-svc start lock held by another process, aborting" >&2
        return 1
    fi
    rm -f "$SOCK_PATH"
    nohup $ENTRY >> "$BOOT_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 1
    if is_running; then
        echo "fusion-project-svc started (pid $pid, sock $SOCK_PATH)"
    else
        echo "fusion-project-svc failed to start, see $BOOT_LOG" >&2
        rm -f "$PID_FILE"
        rmdir "$PID_FILE.lock" 2>/dev/null || true
        return 1
    fi
    rmdir "$PID_FILE.lock" 2>/dev/null || true
}

do_stop() {
    if ! is_running; then
        echo "fusion-project-svc not running"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE" "$SOCK_PATH"
    rmdir "$PID_FILE.lock" 2>/dev/null || true
    exec 9>&- 2>/dev/null || true
    echo "fusion-project-svc stopped"
}

do_status() {
    if is_running; then
        echo "fusion-project-svc running (pid $(cat "$PID_FILE"), sock $SOCK_PATH)"
    else
        echo "fusion-project-svc stopped"
        return 1
    fi
}

case "${1:-status}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" >&2; exit 1 ;;
esac
