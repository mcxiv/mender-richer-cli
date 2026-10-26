"""MessagePack envelopes for Mender shell and port-forward protocols."""

from __future__ import annotations

from typing import Any

import msgpack

PROTO_SHELL = 1
PROTO_PORT_FORWARD = 3
PROTO_PORT_FORWARD_V2 = PROTO_PORT_FORWARD + 0x100
PROTO_CONTROL = 0xFFFF


def message(protocol: int, kind: str, session: str = "", body: bytes = b"",
            properties: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a Mender protocol envelope, omitting empty optional fields.

    :param protocol: Numeric Mender protocol identifier.
    :param kind: Message type; omitted from the header when empty.
    :param session: Session identifier; omitted when empty.
    :param body: Encoded payload; omitted when empty.
    :param properties: Optional header properties.
    :returns: The header and optional body ready for MessagePack encoding.
    """
    header: dict[str, Any] = {"proto": protocol}
    if kind:
        header["typ"] = kind
    if session:
        header["sid"] = session
    if properties:
        header["props"] = properties
    result: dict[str, Any] = {"hdr": header}
    if body:
        result["body"] = body
    return result


def pack_message(value: dict[str, Any]) -> bytes:
    """Encode a protocol envelope using MessagePack binary types.

    :param value: Protocol envelope to serialize.
    :returns: The encoded binary message.
    """
    return msgpack.packb(value, use_bin_type=True)


def unpack_message(value: bytes) -> dict[str, Any]:
    """Decode a MessagePack envelope and require a mapping-valued header.

    :param value: Encoded protocol message.
    :returns: The decoded envelope.
    :raises ValueError: The decoded value is not a mapping with a mapping-valued ``hdr``.
    """
    result = msgpack.unpackb(value, raw=False, strict_map_key=False)
    if not isinstance(result, dict) or not isinstance(result.get("hdr"), dict):
        raise ValueError("invalid Mender protocol message")
    return result


def body_value(value: dict[str, Any]) -> Any:
    """Read an existing body mapping or decode a MessagePack body.

    :param value: Protocol envelope containing an optional body.
    :returns: The decoded payload, or an empty mapping for an absent or empty body.
    """
    body = value.get("body", b"")
    if isinstance(body, dict):
        return body
    return msgpack.unpackb(body, raw=False, strict_map_key=False) if body else {}
