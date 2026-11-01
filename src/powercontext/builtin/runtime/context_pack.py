# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Incremental Context Pack encoding for reused prepare payloads."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Protocol or chunk validation failure for Context Pack wire values."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64_encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64_decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise ContextPackError("base64 item must be a string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:  # noqa: BLE001 - normalize decode failures
        raise ContextPackError("invalid base64 item") from exc


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [_b64_encode(item) for item in items],
    }
    return "sha256:" + hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _encode_pack(items: tuple[bytes, ...], digest: str, byte_count: int) -> bytes:
    return _canonical_json(
        {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": [_b64_encode(item) for item in items],
            "digest": digest,
            "byte_count": byte_count,
        }
    )


def _stabilize_byte_count(items: tuple[bytes, ...], digest: str) -> tuple[bytes, int]:
    byte_count = 0
    wire = b""
    for _ in range(16):
        wire = _encode_pack(items, digest, byte_count)
        if len(wire) == byte_count:
            return wire, byte_count
        byte_count = len(wire)
    raise ContextPackError("unable to stabilize pack byte_count")


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete serialized Context Pack with digest and exact byte count."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...] | list[bytes], /) -> ContextPack:
        normalized = tuple(bytes(item) for item in items)
        digest = _pack_digest(normalized)
        _, byte_count = _stabilize_byte_count(normalized, digest)
        return cls(items=normalized, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if content is None or content == "":
            return cls.build(())
        if not isinstance(content, str):
            raise ContextPackError("content must be a string or None")
        raw = content.encode("utf-8")
        chunks: list[bytes] = []
        index = 0
        while index < len(raw):
            end = min(index + chunk_bytes, len(raw))
            while end > index:
                chunk = raw[index:end]
                try:
                    chunk.decode("utf-8")
                except UnicodeDecodeError:
                    end -= 1
                    continue
                if len(chunk) > chunk_bytes:
                    end -= 1
                    continue
                chunks.append(chunk)
                index = end
                break
            else:
                raise ContextPackError("unable to form a valid UTF-8 chunk within the limit")
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _encode_pack(self.items, self.digest, self.byte_count)

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPack:
        try:
            payload = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextPackError("invalid pack encoding") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack payload fields are invalid")
        if payload.get("schema") != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")
        items_raw = payload.get("items")
        if not isinstance(items_raw, list):
            raise ContextPackError("pack items must be a list")
        items = tuple(_b64_decode(item) for item in items_raw)
        digest = payload.get("digest")
        byte_count = payload.get("byte_count")
        expected_digest = _pack_digest(items)
        if digest != expected_digest:
            raise ContextPackError("pack digest mismatch")
        pack = cls.build(items)
        if byte_count != pack.byte_count or value != pack.to_bytes():
            raise ContextPackError("pack byte_count mismatch")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Delta that rebuilds a target pack from a known base pack prefix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        shared = 0
        limit = min(len(base.items), len(target.items))
        while shared < limit and base.items[shared] == target.items[shared]:
            shared += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=shared,
            appended=target.items[shared:],
        )

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": CONTEXT_PACK_DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": [_b64_encode(item) for item in self.appended],
            }
        )

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPackDelta:
        try:
            payload = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextPackError("invalid delta encoding") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "base_digest",
            "target_digest",
            "prefix_items",
            "appended",
        }:
            raise ContextPackError("delta payload fields are invalid")
        if payload.get("schema") != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")
        prefix = payload.get("prefix_items")
        if type(prefix) is not int or isinstance(prefix, bool) or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        appended_raw = payload.get("appended")
        if not isinstance(appended_raw, list):
            raise ContextPackError("appended must be a list")
        base_digest = payload.get("base_digest")
        target_digest = payload.get("target_digest")
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("digests must be strings")
        appended = tuple(_b64_decode(item) for item in appended_raw)
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Validate and rebuild the target pack from ``base`` and ``delta``."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix_items exceeds base pack length")
    items = base.items[: delta.prefix_items] + delta.appended
    pack = ContextPack.build(items)
    if pack.digest != delta.target_digest:
        raise ContextPackError("delta target_digest mismatch")
    return pack


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
