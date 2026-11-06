# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Self-contained UTF-8 context packs and verified prefix/suffix wire deltas."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """Invalid context protocol data or an impossible UTF-8 chunk boundary."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ContextPackError(detail)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_items(items: tuple[bytes, ...]) -> None:
    _require(isinstance(items, tuple), "Context chunks must be a tuple")
    for item in items:
        _require(isinstance(item, bytes), "Each context chunk must be bytes")
        try:
            item.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ContextPackError("Each context chunk must contain complete UTF-8 characters") from error  # noqa: TRY003


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("Wire chunks must be a Base64 string array")  # noqa: TRY003
    items: list[bytes] = []
    for encoded in value:
        _require(isinstance(encoded, str), "Wire chunks must be Base64 strings")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ContextPackError("Invalid Base64 context chunk") from error  # noqa: TRY003
        _require(base64.b64encode(decoded).decode("ascii") == encoded, "Non-canonical Base64 context chunk")
        items.append(decoded)
    result = tuple(items)
    _validate_items(result)
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate context wire field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContextPackError(f"Non-JSON context value: {value}")  # noqa: TRY003


def _decode_wire(content: bytes, fields: set[str], schema: str) -> dict[str, Any]:
    _require(isinstance(content, bytes), "Context wire payload must be bytes")
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ContextPackError("Invalid UTF-8 JSON context payload") from error  # noqa: TRY003
    _require(isinstance(value, dict) and set(value) == fields, "Missing or unexpected context wire fields")
    _require(value["schema"] == schema, "Unsupported context wire schema")
    return value


def _pack_metadata(items: tuple[bytes, ...]) -> tuple[str, int]:
    unsigned = {"schema": _PACK_SCHEMA, "items": _encode_items(items)}
    digest = f"sha256:{hashlib.sha256(_canonical(unsigned)).hexdigest()}"
    payload = {**unsigned, "digest": digest, "byte_count": 0}
    # The byte_count field contributes its own decimal digits to the encoded length.
    while True:
        length = len(_canonical(payload))
        if payload["byte_count"] == length:
            return digest, length
        payload["byte_count"] = length


@dataclass(frozen=True, slots=True)
class ContextPack:
    """An immutable complete context value with an independently verifiable wire identity."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        _validate_items(self.items)
        digest, length = _pack_metadata(self.items)
        _require(self.digest == digest, "Context pack digest does not match its chunks")
        _require(type(self.byte_count) is int and self.byte_count == length, "Context pack byte_count is incorrect")

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        _validate_items(items)
        digest, length = _pack_metadata(items)
        return cls(items=items, digest=digest, byte_count=length)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        _require(isinstance(content, str), "Context content must be a string")
        _require(type(chunk_bytes) is int and chunk_bytes > 0, "chunk_bytes must be a positive integer")
        try:
            encoded = content.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ContextPackError("Context content is not valid UTF-8") from error  # noqa: TRY003
        chunks: list[bytes] = []
        start = 0
        while start < len(encoded):
            end = min(start + chunk_bytes, len(encoded))
            while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
                end -= 1
            _require(end > start, "chunk_bytes cannot hold the next UTF-8 character")
            chunks.append(encoded[start:end])
            start = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": _PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, content: bytes) -> ContextPack:
        payload = _decode_wire(content, {"schema", "items", "digest", "byte_count"}, _PACK_SCHEMA)
        pack = cls.build(_decode_items(payload["items"]))
        _require(payload["digest"] == pack.digest, "Context pack digest does not match its chunks")
        _require(
            type(payload["byte_count"]) is int and payload["byte_count"] == pack.byte_count == len(content),
            "Context pack byte_count does not match its wire size",
        )
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A complete target suffix anchored to a specific base pack and target digest."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        for digest in (self.base_digest, self.target_digest):
            _require(isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None, "Invalid context digest")
        _require(
            type(self.prefix_items) is int and self.prefix_items >= 0, "Context prefix must be a non-negative integer"
        )
        _validate_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        _require(
            isinstance(base, ContextPack) and isinstance(target, ContextPack), "Delta endpoints must be context packs"
        )
        prefix = 0
        while prefix < min(len(base.items), len(target.items)) and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, content: bytes) -> ContextPackDelta:
        payload = _decode_wire(
            content, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}, _DELTA_SCHEMA
        )
        return cls(
            payload["base_digest"],
            payload["target_digest"],
            payload["prefix_items"],
            _decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Reconstruct the exact target using only previously decoded base and delta data."""
    _require(isinstance(base, ContextPack) and isinstance(delta, ContextPackDelta), "Invalid context delta operands")
    _require(base.digest == delta.base_digest, "Context delta base digest mismatch")
    _require(delta.prefix_items <= len(base.items), "Context delta prefix exceeds the base pack")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    _require(target.digest == delta.target_digest, "Context delta target digest mismatch")
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
