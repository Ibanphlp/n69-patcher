"""Wrapper for img4lib's img4 (tihmstar's) - IMG4 signing with IM4M (APTicket).

This wraps the `img4` binary from tihmstar's img4lib. NOT img4tool (different tool).
On macOS the Darwin build is used; on Linux the Linux build is used.
"""

from __future__ import annotations

import subprocess
import tempfile
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class Img4Tool:
    """Wrapper for img4 binary operations."""

    binary: Path
    im4m: Optional[Path] = None

    def __post_init__(self):
        if not self.binary.is_file():
            raise FileNotFoundError(f"img4 binary not found: {self.binary}")
        if not self.binary.stat().st_mode & 0o111:
            raise PermissionError(f"img4 binary not executable: {self.binary}")

    def _run(self, args: list[str], check: bool = True, use_im4m: bool = True) -> subprocess.CompletedProcess:
        """Run img4 with given arguments."""
        cmd = [str(self.binary)] + args
        if use_im4m and self.im4m and self.im4m.exists():
            cmd += ["-M", str(self.im4m)]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def extract(self, input_img4: Path, output: Path) -> Path:
        """Extract raw payload from IMG4/IM4P."""
        # Use simple extraction without IM4M
        args = ["-i", str(input_img4), "-o", str(output)]
        self._run(args, use_im4m=False)
        return output

    def sign(self, payload: Path, output: Path, tag: str, description: str = "") -> Path:
        """Create an IM4P from raw payload, signed with IM4M.

        img4 syntax: img4 -i <payload> -A -T <tag> -M <IM4M> -o <output>
        """
        args = ["-i", str(payload), "-A", "-T", tag, "-o", str(output)]
        if description:
            # img4 doesn't have -d flag in this version
            pass
        self._run(args)
        return output

    def sign_replace(self, input_img4: Path, output_img4: Path, new_payload: Path, tag: str) -> Path:
        """Replace payload in existing IMG4 and re-sign with IM4M.

        img4 syntax: img4 -i <input> -R <payload> -T <tag> -M <IM4M> -o <output>
        """
        args = ["-i", str(input_img4), "-R", str(new_payload), "-T", tag, "-o", str(output_img4)]
        self._run(args)
        return output_img4

    def sign_kernelcache(self, payload: Path, output: Path) -> Path:
        """Sign large kernelcache using img4tool (handles >20MB payloads where img4 fails)."""
        tool_candidates = [
            Path("ramdisk-main/Darwin/img4tool"),
            Path("ramdisk-main/Linux/img4tool"),
            Path("tools/img4tool/img4tool"),
            Path("img4tool"),
        ]
        if not sys.platform.startswith("darwin"):
            tool_candidates = [
                Path("ramdisk-main/Linux/img4tool"),
                Path("ramdisk-main/Darwin/img4tool"),
                Path("tools/img4tool/img4tool"),
                Path("img4tool"),
            ]
        tool_path = None
        for c in tool_candidates:
            if c.is_file():
                tool_path = c
                break
        if not tool_path:
            import shutil
            tool_path = Path(shutil.which("img4tool") or "img4tool")

        if self.im4m is None or not self.im4m.is_file():
            raise ValueError("A device IM4M is required to package the kernel IMG4")
        # img4tool creates an IM4P (and silently ignores -m) when given raw
        # input. Create the IM4P first, then explicitly combine it and IM4M.
        with tempfile.TemporaryDirectory() as tmp:
            im4p = Path(tmp) / "kernel.im4p"
            subprocess.run([str(tool_path), "-c", str(im4p), "-t", "rkrn", str(payload)], check=True)
            subprocess.run([str(tool_path), "-c", str(output), "-p", str(im4p),
                            "-m", str(self.im4m)], check=True)
        return output

    def wrap_trustcache(self, payload: Path, output: Path) -> Path:
        """Wrap a trust cache payload as rtsc IMG4."""
        return self.sign(payload, output, "rtsc", "TrustCache")

    def extract_im4m(self, nor_dump: Path, output: Path) -> Path:
        """Extract IM4M (APTicket) from NOR dump."""
        args = ["-i", str(nor_dump), "-m", str(output)]
        self._run(args, use_im4m=False)
        return output

    def info(self, img4: Path) -> dict:
        """Get IMG4/IM4P info."""
        # img4 doesn't have -a flag like img4tool
        return {}


def get_img4_tool(im4m_path: Optional[Path] = None, *, project_dir: Path = Path(".")) -> Img4Tool:
    """Get img4 instance, searching standard locations.

    Ordering is host-aware: on Darwin, macOS binaries are preferred; on Linux,
    the Linux binaries. Non-native binaries are skipped to avoid exec format
    errors (e.g. an ELF img4 on macOS).
    """
    import shutil
    import sys

    is_macos = sys.platform.startswith("darwin")

    # Darwin/macOS binaries first (preferred on macOS)
    candidates = [
        Path("ramdisk-main/Darwin/img4"),
        Path("ramdisk-main/Linux/img4"),
        Path("tools/img4lib/img4"),
        Path("tools/img4"),
        Path("img4"),
    ]
    # On macOS, put the Darwin candidates first; on Linux, Linux first.
    if not is_macos:
        candidates = [
            Path("ramdisk-main/Linux/img4"),
            Path("ramdisk-main/Darwin/img4"),
            Path("tools/img4lib/img4"),
            Path("tools/img4"),
            Path("img4"),
        ]

    for relative_path in candidates:
        c = project_dir / relative_path
        if not c.is_file():
            continue
        # Skip non-native binaries: an ELF won't run on macOS and vice versa.
        try:
            header = c.read_bytes()[:4]
        except OSError:
            continue
        is_elf = header == b"\x7fELF"
        is_macho = header in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                              b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf")
        if is_macos and is_elf:
            continue
        if not is_macos and is_macho:
            continue
        if c.stat().st_mode & 0o111:
            return Img4Tool(c.resolve(), im4m_path)

    # Try PATH
    img4_path = shutil.which("img4")
    if img4_path:
        return Img4Tool(Path(img4_path), im4m_path)

    raise FileNotFoundError(
        "img4 not found. Expected at ramdisk-main/Darwin/img4 or ramdisk-main/Linux/img4"
    )
