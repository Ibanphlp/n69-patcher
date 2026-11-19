#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd -P)"
exec "$root/create_mobile_package.sh" "${1:-${TMPDIR:-/tmp}/n69ap-ios16-mac-audit-$(date +%Y%m%d).tar.gz}"
