"""Create the portable N69 host audit archive without reading device state."""

from pathlib import Path
import argparse
import os
import tarfile
import tempfile


N69 = ("__init__", "driver_transplant", "macho", "prelink", "touch_audit")
SEVENUP = ("__init__", "console", "core", "kc", "kernel_base", "kernel_chestnut",
           "kernel_d2333", "kernel_transplant", "process")
FILES = (
    "LICENSE", "FINAL_AUDIT_TABLE.md", "research/audit_n69_transplants.py",
    "research/n69-reference-metadata.json", "research/n69-compatibility-report.json",
    "SevenUP-main/LICENSE", "SevenUP-main/THIRD_PARTY.md",
    *(f"src/ios16se/{name}.py" for name in N69),
    *(f"SevenUP-main/src/iphone7_port/{name}.py" for name in SEVENUP),
)


def package(root, output):
    root = root.resolve()
    sources = [root / name for name in FILES]
    for source in sources:
        if source.resolve() != source or not source.is_file():
            raise ValueError(f"Expected regular source file: {source.relative_to(root)}")
    if output.resolve() in sources:
        raise ValueError("Output path refers to a required input")
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            for name, source in zip(FILES, sources):
                with source.open("rb") as stream:
                    entry = tarfile.TarInfo(name)
                    entry.size = os.fstat(stream.fileno()).st_size
                    entry.mode = 0o644
                    archive.addfile(entry, stream)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        package(Path(__file__).resolve().parents[1], args.output)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Package failed: {error}\n")
    print(f"Host audit archive: {args.output}")
