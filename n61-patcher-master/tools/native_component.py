"""Extract a board-specific component from an IPSW BuildManifest."""
import argparse
from pathlib import Path, PurePosixPath
import plistlib
import zipfile


def extract(ipsw, board, build, component, output):
    with zipfile.ZipFile(ipsw) as archive:
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != build:
            raise ValueError('IPSW build does not match the target manifest')
        paths = {
            identity['Manifest'][component]['Info']['Path']
            for identity in manifest['BuildIdentities']
            if identity['Info']['DeviceClass'].lower() == board.lower()
            and component in identity['Manifest']
        }
        if len(paths) != 1:
            raise ValueError(f'Expected one {component} for {board}, found {sorted(paths)}')
        member = paths.pop()
        if PurePosixPath(member).is_absolute() or '..' in PurePosixPath(member).parts:
            raise ValueError('Invalid component path')
        payload = archive.read(member)
        if not payload:
            raise ValueError('Empty firmware component')
        Path(output).write_bytes(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ipsw', 'board', 'build', 'component', 'output'):
        parser.add_argument(name)
    args = parser.parse_args()
    extract(args.ipsw, args.board, args.build, args.component, args.output)
