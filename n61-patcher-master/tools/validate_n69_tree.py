"""Reject a DeviceTree belonging to another device before signing it."""
import sys
from ios16se.devicetree import inspect, property_value


def validate(path):
    tree = inspect(path)
    compatible = (property_value(tree.root, 'compatible') or b'').split(b'\0')
    model = (property_value(tree.root, 'model') or b'').rstrip(b'\0')
    if b'N69AP' not in compatible or model != b'iPhone8,4':
        raise ValueError('Expected N69AP / iPhone8,4 native DeviceTree')
    return tree


if __name__ == '__main__':
    validate(sys.argv[1])
