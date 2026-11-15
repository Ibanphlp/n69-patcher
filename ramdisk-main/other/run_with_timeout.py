"""Run a USB operation with a wall-clock limit, preserving its output."""
import subprocess
import sys

try:
    result = subprocess.run(sys.argv[2:], timeout=float(sys.argv[1]))
except subprocess.TimeoutExpired:
    print("[-] USB operation timed out. Stop other device tools, reconnect and re-enter DFU, then retry.", file=sys.stderr)
    sys.exit(124)
except KeyboardInterrupt:
    sys.exit(130)
else:
    sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
