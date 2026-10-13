#!/usr/bin/env bash
# Git Bash and WSL launcher. Windows Python is required for the registry.
set -euo pipefail

source="${BASH_SOURCE[0]}"
while [ -L "$source" ]; do
  dir="$(cd -P "$(dirname "$source")" && pwd)"
  source="$(readlink "$source")"
  case "$source" in
    /*) ;;
    *) source="$dir/$source" ;;
  esac
done
dir="$(cd -P "$(dirname "$source")" && pwd)"
script="$dir/gdopen.py"

pick_python() {
  if command -v python >/dev/null 2>&1; then
    echo python
  elif command -v python3 >/dev/null 2>&1; then
    echo python3
  else
    return 1
  fi
}

uname_s="$(uname -s)"
case "$uname_s" in
  MINGW*|MSYS*|CYGWIN*)
    if command -v cygpath >/dev/null 2>&1; then
      script="$(cygpath -w "$script")"
    fi
    if ! py="$(pick_python)"; then
      echo "Python 3 が見つかりません。" >&2
      exit 1
    fi
    export MSYS_NO_PATHCONV=1
    export MSYS2_ARG_CONV_EXCL='*'
    exec "$py" "$script" "$@"
    ;;
  Linux*)
    if [ -z "${WSL_DISTRO_NAME:-}" ] && ! grep -qi microsoft /proc/version 2>/dev/null; then
      echo "Linux では使えません。Git Bash または WSL から起動してください。" >&2
      exit 1
    fi
    if command -v wslpath >/dev/null 2>&1; then
      script="$(wslpath -w "$script")"
    fi
    if command -v python.exe >/dev/null 2>&1; then
      py=python.exe
    elif command -v python3.exe >/dev/null 2>&1; then
      py=python3.exe
    else
      echo "Windows の Python 3 が見つかりません。" >&2
      exit 1
    fi
    exec "$py" "$script" "$@"
    ;;
  *)
    echo "Git Bash または WSL から起動してください。" >&2
    exit 1
    ;;
esac
