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
    # M13: graceful drain. SIGTERM lets the daemon finish in-flight requests
    # (serve() catches SIGTERM -> stop_event -> server.close() -> gateway close).
    # poll up to 30s for the process to exit so a busy service isn't SIGKILLed
    # mid-request; only force-kill if it hangs past the drain window.
    kill -TERM "$pid" 2>/dev/null || true
    local drained=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$drained" -ge 60 ]; then
            echo "drain exceeded 30s, force-killing pid $pid"
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
        sleep 0.5
        drained=$((drained + 1))
    done
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

# M9: online backup. sqlite3 .backup takes a consistent snapshot of a WAL db
# without blocking writers (safe while the daemon is running); the storage
# tree is tarred alongside. output dir defaults under the data home so backups
# live with the data they protect. pass a destination dir as $2 to override.
do_backup() {
    local out_dir="${2:-}"
    if [ -z "$out_dir" ]; then
        local home_dir
        home_dir="${FUSION_PROJECT_HOME:-$HOME/.fusion-projects}"
        out_dir="$home_dir/backups"
    fi
    mkdir -p "$out_dir"
    local ts
    # portable seconds-since-epoch; the stamp is the backup identity.
    ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo "manual")"
    local dest="$out_dir/backup-$ts"
    mkdir -p "$dest"
    local home_dir
    home_dir="${FUSION_PROJECT_HOME:-$HOME/.fusion-projects}"
    local db="$home_dir/data/projects.db"
    local storage="$home_dir/storage"
    if [ ! -f "$db" ]; then
        echo "backup: db not found at $db, nothing to back up" >&2
        return 1
    fi
    if ! command -v sqlite3 >/dev/null 2>&1; then
        echo "backup: sqlite3 not installed, cannot take online snapshot" >&2
        return 1
    fi
    # online backup: safe under concurrent writers (WAL). never copy the db
    # file directly — a live copy can catch a half-written page.
    sqlite3 "$db" ".backup '$dest/projects.db'" || {
        echo "backup: sqlite3 .backup failed" >&2
        return 1
    }
    if [ -d "$storage" ]; then
        tar -C "$home_dir" -czf "$dest/storage.tar.gz" storage 2>/dev/null || {
            echo "backup: storage tar failed (continuing with db snapshot)" >&2
        }
    fi
    local size
    size="$(du -sh "$dest" 2>/dev/null | cut -f1 || echo "?")"
    echo "backup complete: $dest ($size)"
}

case "${1:-status}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    backup)  do_backup "$@" ;;
    *) echo "Usage: $0 {start|stop|restart|status|backup [dest_dir]}" >&2; exit 1 ;;
esac
