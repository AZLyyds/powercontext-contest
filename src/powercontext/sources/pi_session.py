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

"""Pi session branch Sources: captured visible events with incremental connectors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"


class PiSessionInput(BaseModel):
    """Offline Pi v3 JSONL bytes plus an optional selected leaf identity."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi session event ready for Source materialization."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Source for one visible Pi session event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable ``pi:{session}:{event}`` identity with percent-encoded parts."""

    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise ValueError("session_id must be a non-empty string without surrounding whitespace")
    if not isinstance(event_id, str) or not event_id or event_id != event_id.strip():
        raise ValueError("event_id must be a non-empty string without surrounding whitespace")
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def _record_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text_blocks(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            if not isinstance(name, str) or not isinstance(call_id, str):
                continue
            arguments = block.get("arguments", {})
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(arguments)}")
    if not parts:
        return None
    return "\n".join(parts)


def _visible_from_record(record: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(role, body)`` when the record has visible text evidence."""

    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None

    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if isinstance(summary, str):
            return event_type, summary
        return None

    if event_type == "custom_message":
        body = _text_blocks(record.get("content"))
        if body is None:
            return None
        return "custom", body

    if event_type != "message":
        return None

    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str):
        return None

    if role == "toolResult":
        tool_name = message.get("toolName")
        tool_call_id = message.get("toolCallId")
        if not isinstance(tool_name, str) or not isinstance(tool_call_id, str):
            return None
        body_text = _text_blocks(message.get("content"))
        if body_text is None:
            body_text = ""
        return role, f"[tool_result {tool_name} {tool_call_id}] {body_text}"

    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        if not isinstance(command, str):
            return None
        output_text = output if isinstance(output, str) else ""
        return role, f"$ {command}\n{output_text}"

    body = _text_blocks(message.get("content"))
    if body is None:
        return None
    return role, body


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse a Pi v3 JSONL archive and return visible events on the selected branch."""

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Pi session content must be valid UTF-8") from exc

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session must include a session header")

    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Pi session JSON: {exc}") from exc

    if not isinstance(header, dict):
        raise ValueError("Pi session header must be a JSON object")
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must be type=session version=3")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header id must be a non-empty string")

    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in lines[1:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Pi session JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError("Pi session records must be JSON objects")
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi session records must include a non-empty id")
        if event_id in nodes:
            raise ValueError("Pi session event ids must be unique")
        parent_id = record.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("parentId must be a string or null")
        if parent_id is not None and parent_id not in nodes:
            raise ValueError("parentId must reference an earlier event")
        if parent_id == event_id:
            raise ValueError("Pi session graph must not contain self-cycles")
        nodes[event_id] = record
        order.append(event_id)

    if not order:
        raise ValueError("Pi session must include at least one event")

    leaf_id = value.leaf_id
    if leaf_id is None:
        leaf_id = order[-1]
    elif leaf_id not in nodes:
        raise ValueError("leaf_id must reference an existing event")

    # Walk ancestors root→leaf; detect cycles defensively.
    chain_ids: list[str] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("Pi session graph must not contain cycles")
        seen.add(current)
        chain_ids.append(current)
        parent = nodes[current].get("parentId")
        current = parent if isinstance(parent, str) else None
    chain_ids.reverse()

    events: list[PiEventInput] = []
    for event_id in chain_ids:
        record = nodes[event_id]
        visible = _visible_from_record(record)
        if visible is None:
            continue
        role, body = visible
        parent_id = record.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            parent_id = None
        event_type = record["type"]
        assert isinstance(event_type, str)
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parent_id if isinstance(parent_id, str) else None,
                event_type=event_type,
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read captured Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Project a Pi event as full text evidence with identity metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "session_id": source.session_id,
            "event_id": source.event_id,
            "parent_id": source.parent_id,
            "event_type": source.event_type,
            "role": source.role,
        }
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata=metadata,
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version="1",
    projections=(PiEventTextEvidenceProjection(),),
)


def _load_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi session checkpoint must be a JSON object")
    events = checkpoint.get("events")
    if events is None:
        return {}
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint events must be an object")
    loaded: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint entries must be string digests")
        loaded[event_id] = digest
    return loaded


class PiSessionConnector:
    """Incrementally import visible Pi branch events through ConnectorLifecycle."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=content, leaf_id=self._leaf_id))
        imported = _load_checkpoint(session.checkpoint)
        next_events = dict(imported)

        for event in events:
            digest = _record_digest(event.record)
            previous = imported.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError(
                        f"imported Pi event {event.event_id!r} was rewritten after capture"
                    )
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            next_events[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"events": next_events},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
