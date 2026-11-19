#!/bin/bash
# Package the portable host-side audit sources using a closed list of inputs.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd -P)"
output="${1:-${TMPDIR:-/tmp}/n69ap-ios16-audit-$(date +%Y%m%d).tar.gz}"
exec python3 "$root/research/package_audit.py" "$output"
