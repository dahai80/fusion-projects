#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
PID_FILE="$SCRIPT_DIR/.fusion-project-svc.pid"
LOG_DIR="$SCRIPT_DIR/logs"
STDOUT_LOG="$LOG_DIR/stdout.log"
STDERR_LOG="$LOG_DIR/stderr.log"
ENTRY="python3 -m project_service.daemon_server"
SOCK_PATH="${FUSION_PROJECT_SOCK:-/tmp/fusion-project-svc.sock}"

mkdir -p "$LOG_DIR"

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

do_start() {
    if is_running; then
        echo "fusion-project-svc already running (pid $(cat "$PID_FILE"))"
        return 0
    fi
    if [ -d "$VENV_DIR" ]; then
        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"
    fi
    rm -f "$SOCK_PATH"
    nohup $ENTRY >> "$STDOUT_LOG" 2>> "$STDERR_LOG" &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 1
    if is_running; then
        echo "fusion-project-svc started (pid $pid, sock $SOCK_PATH)"
    else
        echo "fusion-project-svc failed to start, see $STDERR_LOG" >&2
        rm -f "$PID_FILE"
        return 1
    fi
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
