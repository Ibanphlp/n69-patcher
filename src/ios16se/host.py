"""Read-only host and USB readiness checks for an N69AP DFU session."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


APPLE_VENDOR = "05ac"
DFU_PRODUCT = "1227"
PONGO_PRODUCT = "4141"


@dataclass(frozen=True)
class UsbDevice:
    path: Path
    vendor: str
    product: str


@dataclass(frozen=True)
class HostReadiness:
    tools_present: tuple[str, ...]
    tools_missing: tuple[str, ...]
    apple_devices: tuple[UsbDevice, ...]


def apple_usb_devices(sysfs_root: str | Path = "/sys/bus/usb/devices") -> tuple[UsbDevice, ...]:
    """List Apple USB devices from sysfs; this sends no USB request."""

    root = Path(sysfs_root)
    if not root.is_dir():
        return ()
    devices: list[UsbDevice] = []
    for entry in sorted(root.iterdir()):
        vendor_path = entry / "idVendor"
        product_path = entry / "idProduct"
        if not vendor_path.is_file() or not product_path.is_file():
            continue
        vendor = vendor_path.read_text().strip().lower()
        product = product_path.read_text().strip().lower()
        if vendor == APPLE_VENDOR:
            devices.append(UsbDevice(entry, vendor, product))
    return tuple(devices)


def tool_directories(project_root: str | Path | None = None) -> tuple[Path, ...]:
    """Return deterministic locations for project-local DFU tools."""

    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    configured = tuple(Path(item) for item in os.environ.get("IOS16SE_TOOLS", "").split(os.pathsep) if item)
    return configured + (
        root / "tools" / "gaster" / "bin",
        root / "tools" / "checkra1n" / "bin",

        root / "tools" / "palera1n" / "bin",
        root / "tools" / "irecovery" / "bin",
        root / "tools" / "pongo" / "bin",
    )


def find_tool(name: str, directories: tuple[Path, ...] | None = None) -> str | None:
    """Locate a named executable without relying solely on the global PATH."""

    for directory in directories if directories is not None else tool_directories():
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def usable_tool(name: str, directories: tuple[Path, ...] | None = None) -> str | None:
    """Return an executable only when its required runtime can start it."""

    path = find_tool(name, directories)
    if path is None:
        return None
    if name != "checkra1n":
        return path
    try:
        probe = subprocess.run((path, "--version"), capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return path if probe.returncode == 0 else None


def readiness(
    sysfs_root: str | Path = "/sys/bus/usb/devices", tool_dirs: tuple[Path, ...] | None = None
) -> HostReadiness:
    """Report the local tools and Apple USB modes required by the boot plan."""

    required_tools = ("gaster", "checkra1n", "palera1n", "irecovery", "pongoterm")
    present = tuple(tool for tool in required_tools if usable_tool(tool, tool_dirs) is not None)
    missing = tuple(tool for tool in required_tools if tool not in present)
    return HostReadiness(present, missing, apple_usb_devices(sysfs_root))
