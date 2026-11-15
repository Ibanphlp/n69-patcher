"""Read-only device identification through libimobiledevice."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable, Sequence


class DeviceProbeError(RuntimeError):
    """The host cannot read basic properties from an iPhone in normal mode."""


Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DeviceFacts:
    product_type: str
    hardware_model: str
    product_version: str
    build_version: str


def _property(key: str, run: Run) -> str:
    try:
        result = run(
            ["ideviceinfo", "-k", key],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )
    except FileNotFoundError as error:
        raise DeviceProbeError("ideviceinfo est absent ; installe libimobiledevice-utils") from error
    except subprocess.TimeoutExpired as error:
        raise DeviceProbeError("lecture de l'appareil expirée") from error
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        detail = result.stderr.strip() or "aucun appareil iOS déverrouillé détecté"
        raise DeviceProbeError(detail)
    return value


def probe(run: Run = subprocess.run) -> DeviceFacts:
    """Read identifiers only; this function has no DFU, restore, or write path."""

    return DeviceFacts(
        product_type=_property("ProductType", run),
        hardware_model=_property("HardwareModel", run),
        product_version=_property("ProductVersion", run),
        build_version=_property("BuildVersion", run),
    )
