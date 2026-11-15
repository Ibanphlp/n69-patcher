#!/usr/bin/env python3
"""Validate and select complete stock SEP IMG4 images captured from Preboot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ios16se.sep import (  # noqa: E402
    SepError,
    atomic_write,
    preboot_hash_from_path,
    parse_irecovery_identity,
    receipt,
    select_sep,
    validate_identity,
    validate_image,
    write_receipt,
)


def _number(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value, 0)


def command_select(args: argparse.Namespace) -> int:
    candidates = list(args.files)
    if args.stdin:
        candidates.extend(sys.stdin.read().splitlines())
    try:
        path = select_sep(candidates, args.active, args.requested, mount=args.mount)
    except SepError:
        print(f"Preboot active value (up to 512 characters): {args.active.strip()[:512]!r}", file=sys.stderr)
        print(f"SEP paths found: {len(candidates)}", file=sys.stderr)
        for candidate in candidates[:50]:
            print(f"  {candidate!r}", file=sys.stderr)
        if len(candidates) > 50:
            print(f"  ... {len(candidates) - 50} more paths omitted", file=sys.stderr)
        print("No SEP was selected. Do not guess a slot or share SEP/IM4M files.", file=sys.stderr)
        raise
    print(path)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    path = Path(args.image)
    data = path.read_bytes()

    expected_hash = None
    if args.source:
        expected_hash = preboot_hash_from_path(args.source, mount=args.mount)
    if args.preboot_hash:
        requested = args.preboot_hash.upper()
        if expected_hash is not None and expected_hash != requested:
            raise SepError("--preboot-hash does not match --source")
        expected_hash = requested

    image = validate_image(data, expected_ticket_hash=expected_hash)
    if args.expect_ticket:
        expected_ticket = Path(args.expect_ticket).read_bytes()
        if image.ticket != expected_ticket:
            raise SepError("Captured SEP ticket does not match the requested IM4M file")

    expected_ecid = _number(args.ecid)
    expected_cpid = _number(args.cpid)
    identity = validate_identity(
        image.ticket,
        expected_ecid=expected_ecid,
        expected_cpid=expected_cpid,
        img4tool=args.img4tool,
    )

    if args.ticket_out:
        atomic_write(args.ticket_out, image.ticket)
    if args.receipt_out:
        write_receipt(
            args.receipt_out,
            receipt(
                image,
                source=args.source or str(path),
                preboot_hash=expected_hash,
                expected_ecid=expected_ecid,
                expected_cpid=expected_cpid,
                actual_identity=identity,
            ),
        )

    print(
        f"SEP IMG4 OK: type={image.image_type} "
        f"ticket_sha384={image.preboot_hash} "
        f"preboot_hash={expected_hash or 'unchecked'} "
        f"ecid={expected_ecid if expected_ecid is not None else 'unchecked'} "
        f"cpid={expected_cpid if expected_cpid is not None else 'unchecked'}"
    )
    return 0


def command_verify_device(args: argparse.Namespace) -> int:
    image = validate_image(Path(args.image).read_bytes())
    if args.expect_ticket and image.ticket != Path(args.expect_ticket).read_bytes():
        raise SepError("Captured SEP ticket does not match the requested IM4M file")
    ecid, cpid = parse_irecovery_identity(sys.stdin.read())
    validate_identity(image.ticket, expected_ecid=ecid, expected_cpid=cpid, img4tool=args.img4tool)
    print("SEP ticket matches the connected recovery device")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select one active Preboot SEP image")
    select.add_argument("--mount", default="/mnt8", help="Preboot mount point in the ramdisk")
    select.add_argument("--active", default="", help="contents of <preboot>/active")
    select.add_argument("--requested", help="explicit 40- or 96-hex Preboot slot hash")
    select.add_argument("--stdin", action="store_true", help="also read candidate paths from stdin")
    select.add_argument("files", nargs="*", help="candidate paths")
    select.set_defaults(handler=command_select)

    validate = subparsers.add_parser("validate", help="validate a complete captured SEP IMG4")
    validate.add_argument("--image", required=True)
    validate.add_argument("--source", help="original Preboot path, used to derive the ticket hash")
    validate.add_argument("--mount", default="/mnt8")
    validate.add_argument("--preboot-hash", help="expected SHA-1 or SHA-384 ticket directory name")
    validate.add_argument("--expect-ticket", help="require the embedded ticket to equal this IM4M file")
    validate.add_argument("--ticket-out", help="write the embedded IM4M here after validation")
    validate.add_argument("--receipt-out", help="write a JSON capture receipt here")
    validate.add_argument("--ecid", help="expected live ECID (decimal or 0x-prefixed)")
    validate.add_argument("--cpid", help="expected live CPID (decimal or 0x-prefixed)")
    validate.add_argument("--img4tool", help="img4tool path for ticket identity validation")
    validate.set_defaults(handler=command_validate)

    verify = subparsers.add_parser("verify-device", help="check the embedded ticket against irecovery -q on stdin")
    verify.add_argument("--image", required=True)
    verify.add_argument("--img4tool", required=True)
    verify.add_argument("--expect-ticket")
    verify.set_defaults(handler=command_verify_device)

    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError) as error:
        print(f"[x] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
