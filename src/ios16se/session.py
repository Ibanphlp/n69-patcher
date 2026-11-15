"""Explicit, logged entry point for the PongoOS RAM environment."""

from __future__ import annotations

from pathlib import Path
import subprocess

from .host import usable_tool


class PongoSessionError(ValueError):
    """A Pongo RAM session cannot be started safely on this host."""


def pongo_command(image: str | Path, log: str | Path, executable: str | None = None) -> tuple[str, ...]:
    """Build the exact checkra1n invocation without sending anything to USB."""

    image_path = Path(image)
    log_path = Path(log)
    if not image_path.is_file():
        raise PongoSessionError(f"image Pongo absente : {image_path}")
    if log_path.exists() and log_path.is_dir():
        raise PongoSessionError(f"journal Pongo invalide (répertoire) : {log_path}")
    checkra1n = executable or usable_tool("checkra1n")
    if checkra1n is None:
        raise PongoSessionError("checkra1n exécutable requis pour charger Pongo")
    return (
        checkra1n,
        "--cli",
        "--pongo",
        "--override-pongo",
        str(image_path),
        "--log-path",
        str(log_path),
        "--no-colors",
    )


def palera1n_pongo_command(image: str | Path, executable: str | None = None) -> tuple[str, ...]:
    """Build the Pongo-only palera1n invocation without using USB."""

    image_path = Path(image)
    if not image_path.is_file():
        raise PongoSessionError(f"image Pongo absente : {image_path}")
    palera1n = executable or usable_tool("palera1n")
    if palera1n is None:
        raise PongoSessionError("palera1n exécutable requis pour charger Pongo")
    return (
        palera1n,
        "--pongo-shell",
        "--override-pongo",
        str(image_path),
        "--no-colors",
    )


def start_pongo(image: str | Path, log: str | Path) -> int:
    """Start checkra1n's interactive DFU flow and persist its complete log."""

    command = pongo_command(image, log)
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(command, check=False).returncode


def start_palera1n_pongo(image: str | Path, log: str | Path) -> int:
    """Boot only PongoOS through palera1n; no jailbreak mode is selected."""

    log_path = Path(log)
    if log_path.exists() and log_path.is_dir():
        raise PongoSessionError(f"journal Pongo invalide (répertoire) : {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        return subprocess.run(
            palera1n_pongo_command(image),
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        ).returncode
