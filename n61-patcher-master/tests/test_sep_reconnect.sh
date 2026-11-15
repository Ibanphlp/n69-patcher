#!/bin/bash
# Offline transport/SEP regressions: no USB tools or device commands are run.
set -euo pipefail
source "$(dirname "$0")/../lib/ssh.sh"
log() { :; }
ok() { :; }
warn() { :; }
die() { echo "$*" >&2; exit 1; }
sleep() { :; }
proxy_start() { :; }
have_tool() { return 0; }

# A confirmed session may disappear from USB briefly and then recover.
(
    probes=0
    ssh_alive() { probes=$((probes + 1)); [ "$probes" -ge 4 ]; }
    devices_present() { die 'reconnect consulted recovery USB detection'; }
    device_in_recovery() { die 'reconnect consulted recovery mode'; }
    SSH_CONNECTED=1
    ssh_ensure
    [ "$SSH_CONNECTED" -eq 1 ]
)

# First connection still uses the normal preflight mode.
(
    ssh_alive() { return 1; }
    ssh_wait() { [ "$#" -eq 0 ]; }
    SSH_CONNECTED=0
    ssh_ensure
)

# A returning SSH connection is insufficient: SEP must answer successfully.
(
    pings=0
    sep_ping_ok() { pings=$((pings + 1)); [ "$pings" -ge 4 ]; }
    ssh_run() { return 0; }
    ssh_run_timeout() { return 0; }
    ssh_ensure() { :; }
    ensure_sep
    [ "$pings" -eq 4 ]
)

# Persistent readiness failure must stop before the caller can format.
if (
    sep_ping_ok() { return 1; }
    ssh_run() { return 0; }
    ssh_run_timeout() { return 0; }
    ssh_ensure() { :; }
    ensure_sep
) 2>/dev/null; then
    echo 'SEP failure was accepted' >&2
    exit 1
fi

# Ping calls have a deadline, including the retry after SSH interruption.
(
    ssh_run_timeout() { [ "$1" -eq 15 ] || exit 99; return 255; }
    ssh_ensure() { :; }
    if sep_ping_ok; then exit 1; fi
)
echo 'SEP reconnect regressions passed'
