#!/bin/bash
# Redirected to wasteless CLI — use: wasteless [command]
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wasteless.sh" "$@"
