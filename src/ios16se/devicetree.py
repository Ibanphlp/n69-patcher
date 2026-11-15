"""Defensive reader for Apple's flattened, pre-DTB DeviceTree format."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import struct


PROPERTY_NAME_SIZE = 32
PROPERTY_HEADER_SIZE = PROPERTY_NAME_SIZE + 4
NODE_HEADER_SIZE = 8
VALUE_FLAG_MASK = 0x8000_0000
MAX_NODES = 20_000


class DeviceTreeError(ValueError):
    """An Apple DeviceTree payload is malformed or exceeds parser bounds."""


@dataclass(frozen=True)
class DeviceTreeProperty:
    name: str
    value: bytes
    flags: int


@dataclass(frozen=True)
class DeviceTreeNode:
    name: str
    properties: tuple[DeviceTreeProperty, ...]
    children: tuple["DeviceTreeNode", ...]


@dataclass(frozen=True)
class DeviceTreeInfo:
    root: DeviceTreeNode
    node_count: int
    property_count: int
    consumed_size: int


@dataclass(frozen=True)
class DeviceTreeDelta:
    only_target_nodes: tuple[str, ...]
    only_donor_nodes: tuple[str, ...]
    changed_nodes: tuple[str, ...]
    changed_property_count: int


def _align(value: int) -> int:
    return (value + 3) & ~3


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _property_name(properties: list[DeviceTreeProperty], fallback: str) -> str:
    for property_ in properties:
        if property_.name == "name" and property_.value:
            name = _text(property_.value)
            if name:
                return name
    return fallback


def inspect_bytes(data: bytes) -> DeviceTreeInfo:
    """Parse one decompressed Apple DeviceTree without modifying it."""

    node_count = 0
    property_count = 0

    def parse_node(offset: int) -> tuple[DeviceTreeNode, int]:
        nonlocal node_count, property_count
        if node_count >= MAX_NODES:
            raise DeviceTreeError("limite de nœuds DeviceTree dépassée")
        if offset + NODE_HEADER_SIZE > len(data):
            raise DeviceTreeError("en-tête de nœud DeviceTree tronqué")
        property_total, child_total = struct.unpack_from("<II", data, offset)
        if property_total > len(data) // PROPERTY_HEADER_SIZE:
            raise DeviceTreeError("nombre de propriétés DeviceTree invalide")
        node_count += 1
        cursor = offset + NODE_HEADER_SIZE
        properties: list[DeviceTreeProperty] = []
        for _ in range(property_total):
            if cursor + PROPERTY_HEADER_SIZE > len(data):
                raise DeviceTreeError("en-tête de propriété DeviceTree tronqué")
            name = _text(data[cursor : cursor + PROPERTY_NAME_SIZE])
            size_and_flags = struct.unpack_from("<I", data, cursor + PROPERTY_NAME_SIZE)[0]
            size = size_and_flags & ~VALUE_FLAG_MASK
            value_start = cursor + PROPERTY_HEADER_SIZE
            value_end = value_start + size
            if value_end > len(data):
                raise DeviceTreeError("valeur de propriété DeviceTree tronquée")
            properties.append(DeviceTreeProperty(name, data[value_start:value_end], size_and_flags & VALUE_FLAG_MASK))
            property_count += 1
            cursor = _align(value_end)
            if cursor > len(data):
                raise DeviceTreeError("alignement de propriété DeviceTree invalide")
        name = _property_name(properties, f"unnamed@{offset:x}")
        children: list[DeviceTreeNode] = []
        for _ in range(child_total):
            child, cursor = parse_node(cursor)
            children.append(child)
        return DeviceTreeNode(name, tuple(properties), tuple(children)), cursor

    root, consumed = parse_node(0)
    if consumed != len(data):
        raise DeviceTreeError(f"données DeviceTree non consommées : {len(data) - consumed} octets")
    return DeviceTreeInfo(root, node_count, property_count, consumed)


def inspect(path: str | Path) -> DeviceTreeInfo:
    return inspect_bytes(Path(path).read_bytes())


def property_value(node: DeviceTreeNode, name: str) -> bytes | None:
    """Return one node property value, if present."""

    for property_ in node.properties:
        if property_.name == name:
            return property_.value
    return None


def _nodes(info: DeviceTreeInfo) -> dict[str, DeviceTreeNode]:
    result: dict[str, DeviceTreeNode] = {}

    def visit(node: DeviceTreeNode, path: str) -> None:
        result[path] = node
        counts: dict[str, int] = {}
        for child in node.children:
            counts[child.name] = counts.get(child.name, 0) + 1
            suffix = "" if counts[child.name] == 1 else f"[{counts[child.name]}]"
            visit(child, f"{path}/{child.name}{suffix}")

    visit(info.root, f"/{info.root.name}")
    return result


def _property_signature(node: DeviceTreeNode) -> dict[str, tuple[int, int, str]]:
    return {
        property_.name: (len(property_.value), property_.flags, sha256(property_.value).hexdigest())
        for property_ in node.properties
    }


def diff(target: DeviceTreeInfo, donor: DeviceTreeInfo) -> DeviceTreeDelta:
    """Compare node paths and complete property values between two trees."""

    target_nodes = _nodes(target)
    donor_nodes = _nodes(donor)
    common = sorted(set(target_nodes) & set(donor_nodes))
    changed_nodes: list[str] = []
    changed_properties = 0
    for path in common:
        target_properties = _property_signature(target_nodes[path])
        donor_properties = _property_signature(donor_nodes[path])
        differing = set(target_properties) ^ set(donor_properties)
        differing.update(
            name
            for name in set(target_properties) & set(donor_properties)
            if target_properties[name] != donor_properties[name]
        )
        if differing:
            changed_nodes.append(path)
            changed_properties += len(differing)
    return DeviceTreeDelta(
        only_target_nodes=tuple(sorted(set(target_nodes) - set(donor_nodes))),
        only_donor_nodes=tuple(sorted(set(donor_nodes) - set(target_nodes))),
        changed_nodes=tuple(changed_nodes),
        changed_property_count=changed_properties,
    )
