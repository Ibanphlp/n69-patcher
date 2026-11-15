"""Minimal USB client for PongoOS' documented raw-image boot command."""

from __future__ import annotations

import ctypes
import ctypes.util
from pathlib import Path
import time


APPLE_VID = 0x05AC
PONGO_PID = 0x4141
DEFAULT_UPLOAD_SIZE = 1024 * 1024
MAX_IMAGE = 128 * 1024 * 1024
USB_WRITE_TIMEOUT_MS = 100_000


class PongoError(RuntimeError):
    """Pongo is unavailable or rejected a host-side operation."""


class _LibUSB:
    def __init__(self) -> None:
        name = ctypes.util.find_library("usb-1.0")
        if not name:
            raise PongoError("libusb-1.0 absente")
        self.lib = ctypes.CDLL(name)
        self.ctx = ctypes.c_void_p()
        self._declare()
        self._check(self.lib.libusb_init(ctypes.byref(self.ctx)), "initialisation libusb")

    def _declare(self) -> None:
        lib = self.lib
        lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        lib.libusb_close.argtypes = [ctypes.c_void_p]
        lib.libusb_set_configuration.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.libusb_control_transfer.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8,
            ctypes.c_uint16, ctypes.c_uint16, ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint]
        lib.libusb_control_transfer.restype = ctypes.c_int
        lib.libusb_bulk_transfer.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p,
            ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_uint]
        lib.libusb_bulk_transfer.restype = ctypes.c_int

    @staticmethod
    def _check(code: int, action: str) -> None:
        if code < 0:
            raise PongoError(f"{action} échouée (libusb {code})")

    def close(self) -> None:
        self.lib.libusb_exit(self.ctx)

    def wait_device(self, timeout: float) -> ctypes.c_void_p:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            handle = self.lib.libusb_open_device_with_vid_pid(self.ctx, APPLE_VID, PONGO_PID)
            if handle:
                return ctypes.c_void_p(handle)
            time.sleep(0.5)
        raise PongoError("Pongo USB 05ac:4141 introuvable (lancer Pongo et pongoterm doit être fermé)")

    def open(self, timeout: float) -> ctypes.c_void_p:
        handle = self.wait_device(timeout)
        # Linux already configures Pongo's sole USB configuration (1) when it
        # enumerates. Sending SET_CONFIGURATION again makes this Pongo build
        # stall with LIBUSB_ERROR_OTHER, so only claim its active interface.
        self._check(self.lib.libusb_claim_interface(handle, 0), "réservation interface USB")
        return handle

    def control(
        self, handle: ctypes.c_void_p, request: int, data: bytes = b"", value: int = 0
    ) -> None:
        # Pongo control request 1 (arm upload) always requires a 4-byte size parameter
        if request == 1 and not data:
            data = DEFAULT_UPLOAD_SIZE.to_bytes(4, "little")
        buf = ctypes.create_string_buffer(data) if data else None
        ptr = ctypes.cast(buf, ctypes.c_void_p) if buf else None
        result = self.lib.libusb_control_transfer(handle, 0x21, request, value, 0, ptr, len(data), 5000)
        self._check(result, f"commande Pongo USB {request}")
        if result != len(data):
            raise PongoError(f"commande Pongo USB {request} tronquée ({result}/{len(data)} octets)")

    def send(self, handle: ctypes.c_void_p, data: bytes) -> None:
        """Issue one USB bulk transaction, as Pongo's own PyUSB client does.

        Pongo arms endpoint 2 for the complete declared upload. Splitting a
        payload makes its DMA callback complete at the first host transfer,
        leaving later chunks with no receiver.
        """
        buf = ctypes.create_string_buffer(data)
        sent = ctypes.c_int()
        result = self.lib.libusb_bulk_transfer(
            handle, 2, buf, len(data), ctypes.byref(sent), USB_WRITE_TIMEOUT_MS
        )
        if result < 0:
            raise PongoError(f"transfert image échoué ({sent.value}/{len(data)} octets, libusb {result})")
        if sent.value != len(data):
            raise PongoError(f"transfert Pongo incomplet ({sent.value}/{len(data)} octets)")


def boot_raw(image: str | Path, timeout: float = 5.0) -> int:
    """Send a raw boot image to Pongo and invoke its ``bootr`` command."""
    path = Path(image)
    if not path.is_file():
        raise PongoError(f"image absente : {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_IMAGE:
        raise PongoError(f"taille d'image invalide : {size}")
    upload_size = (size + 0x1FF) & ~0x1FF
    transport = _LibUSB()
    handle = None
    try:
        handle = transport.open(timeout)
        # Pongo's own boot-checkra1n.py uses this command/data handshake.
        # Match PongoOS' own boot script: synchronize the shell, set boot args,
        # discard any previous upload, then arm the raw-image transfer.
        transport.control(handle, 4)
        transport.control(handle, 3, b"xargs serial=3 rootdev=md0\n")
        transport.control(handle, 2)
        # Pongo allocates a 1 MiB receive buffer at startup. Request 1 with a
        # 4-byte size both resizes it (rounded to 512 bytes, max 128 MiB) and
        # arms the endpoint DMA for the whole image.
        if upload_size > DEFAULT_UPLOAD_SIZE:
            transport.control(handle, 1, upload_size.to_bytes(4, "little"))
        else:
            transport.control(handle, 1)
        transport.send(handle, path.read_bytes())
        transport.control(handle, 4)
        transport.control(handle, 3, b"bootr\n")
        return size
    finally:
        if handle:
            transport.lib.libusb_release_interface(handle, 0)
            transport.lib.libusb_close(handle)
        transport.close()


def load_xnu(image: str | Path, timeout: float = 5.0) -> int:
    """Replace the staged XNU image, then use Pongo's XNU boot path.

    ``loadx`` copies the received Mach-O over iBoot's current kernel location;
    ``bootux`` exits Pongo through its native XNU boot path, preserving the
    N69AP BootArgs and DeviceTree already supplied by iBoot.
    """
    path = Path(image)
    if not path.is_file():
        raise PongoError(f"image absente : {path}")
    payload = path.read_bytes()
    size = len(payload)
    if size == 0 or size > MAX_IMAGE:
        raise PongoError(f"taille d'image invalide : {size}")
    upload_size = (size + 0x1FF) & ~0x1FF
    transport = _LibUSB()
    handle = None
    try:
        handle = transport.open(timeout)
        # Match Pongo's expected handshake: sync, set boot args, discard previous,
        # then arm the endpoint for the full image upload.
        transport.control(handle, 4)  # sync shell
        transport.control(handle, 3, b"xargs serial=3 rootdev=md0\n")  # set boot args
        transport.control(handle, 2)  # discard any previous upload
        if upload_size > DEFAULT_UPLOAD_SIZE:
            transport.control(handle, 1, upload_size.to_bytes(4, "little"))  # arm with size
        else:
            transport.control(handle, 1)  # arm with default 1MiB
        transport.send(handle, payload)  # send kernel Mach-O
        transport.control(handle, 4)  # sync
        transport.control(handle, 3, b"loadx\n")  # copy to iBoot's kernel location
        transport.control(handle, 4)  # sync
        transport.control(handle, 3, b"bootux\n")  # handoff to XNU
        return size
    finally:
        if handle:
            transport.lib.libusb_release_interface(handle, 0)
            transport.lib.libusb_close(handle)
        transport.close()
