#!/usr/bin/env bash
# Drop leftover API/UI processes from a previous crash. Uvicorn --reload can
# keep :8756 after a segfault on Ctrl+C, which then fails with EADDRINUSE.
free_listen_port() {
  local port=$1
  local pids
  pids=$(ss -lptn "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u) || true
  if [ -z "$pids" ]; then
    return 0
  fi
  echo "freeing :$port (already in use)"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.3
  pids=$(ss -lptn "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u) || true
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.2
  fi
}

free_listen_port 8756
free_listen_port 5173
