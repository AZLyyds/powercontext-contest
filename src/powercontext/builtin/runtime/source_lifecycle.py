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

"""Propagate Source availability through Memory and Topic Memory dependencies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, RetireMemoryEntryRequest
from powercontext.sources.models import SourceRef

Policy = Literal["all", "any"]
Purpose = Literal["generation", "audit"]


class SourceLifecycleError(ValueError):
    """Raised for invalid lifecycle registrations, events, or resolution."""


class SourceLifecycleEvent(BaseModel):
    """One versioned availability transition for a registered Source node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty non-blank string")
        return value


_METADATA = MetaData()
_LIFECYCLE_NODES = Table(
    "pc_source_lifecycle_nodes",
    _METADATA,
    Column("scope_id", String(256), primary_key=True),
    Column("node_id", String(256), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("policy", String(8)),
    Column("parents_json", Text),
    Column("reference_json", Text, nullable=False),
    Column("revision", Integer),
    Column("sequence", Integer, nullable=False, server_default="0"),
    Column("available", Integer, nullable=False, server_default="1"),
)


@dataclass
class _Node:
    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...] = ()
    policy: Policy | None = None
    revision: int | None = None
    sequence: int = 0
    available: bool = True


@dataclass
class _ScopeGraph:
    nodes: dict[str, _Node] = field(default_factory=dict)


class SourceLifecycleService:
    """Register dependency DAG nodes and propagate Source availability."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        _require_node_id(node_id)
        if type(revision) is not int or isinstance(revision, bool) or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        await self._ensure_schema()
        existing = await self._load_node(node_id)
        if existing is not None:
            if existing.kind != "source" or existing.reference != source_ref or existing.revision != revision:
                raise SourceLifecycleError(f"conflicting registration for {node_id!r}")
            return
        # Verify the source can be read from the durable journal.
        async with self._runtime._provider.database.transaction() as connection:
            await self._runtime._provider.repositories.sources.get(connection, self.scope_id, source_ref)
        node = _Node(
            node_id=node_id,
            kind="source",
            reference=source_ref,
            revision=revision,
            available=True,
        )
        await self._save_node(node)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: Policy,
    ) -> None:
        await self._register_derived(node_id, "memory", citation, parents=parents, policy=policy)
        # Reflect current availability immediately.
        await self._sync_memory_state(node_id)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Policy,
    ) -> None:
        await self._register_derived(node_id, "topic", artifact_ref, parents=parents, policy=policy)
        await self._sync_topic_state(node_id)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        await self._ensure_schema()
        node = await self._load_node(event.node_id)
        if node is None or node.kind != "source":
            raise SourceLifecycleError(f"unknown Source node {event.node_id!r}")
        if node.revision != event.revision:
            raise SourceLifecycleError("event revision does not match registered Source")
        if event.sequence < node.sequence:
            raise SourceLifecycleError("event sequence must be monotonic")
        if event.sequence == node.sequence:
            if bool(node.available) != bool(event.available):
                raise SourceLifecycleError("replayed event content conflicts")
            return sorted(node_id for node_id, item in (await self._load_graph()).nodes.items() if item.available)
        node.sequence = event.sequence
        node.available = bool(event.available)
        await self._save_node(node)
        await self._propagate()
        graph = await self._load_graph()
        return sorted(node_id for node_id, item in graph.nodes.items() if item.available)

    async def resolve(self, node_id: str, *, purpose: Purpose) -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError(f"unknown purpose {purpose!r}")
        node = await self._load_node(node_id)
        if node is None:
            raise SourceLifecycleError(f"unknown node {node_id!r}")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError(f"node {node_id!r} is unavailable for generation")
        return node.reference

    async def _register_derived(
        self,
        node_id: str,
        kind: Literal["memory", "topic"],
        reference: MemoryCitation | ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Policy,
    ) -> None:
        _require_node_id(node_id)
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("policy must be 'all' or 'any'")
        if not parents:
            raise SourceLifecycleError("derived nodes require at least one parent")
        if len(set(parents)) != len(parents):
            raise SourceLifecycleError("parent list must not contain duplicates")
        if node_id in parents:
            raise SourceLifecycleError("self-parent cycles are forbidden")
        await self._ensure_schema()
        existing = await self._load_node(node_id)
        if existing is not None:
            if (
                existing.kind != kind
                or existing.reference != reference
                or existing.parents != parents
                or existing.policy != policy
            ):
                raise SourceLifecycleError(f"conflicting registration for {node_id!r}")
            return
        graph = await self._load_graph()
        for parent in parents:
            if parent not in graph.nodes:
                raise SourceLifecycleError(f"missing parent {parent!r}")
        # Cycle check: parent paths must not reach node_id (new node).
        if _introduces_cycle(graph, node_id, parents):
            raise SourceLifecycleError("dependency cycle detected")
        # Validate reference readability.
        if kind == "memory":
            assert isinstance(reference, MemoryCitation)
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
        else:
            assert isinstance(reference, ArtifactRef)
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                reference.family, reference.artifact_id, reference.revision
            )
        available = _compute_availability(graph, parents, policy)
        node = _Node(
            node_id=node_id,
            kind=kind,
            reference=reference,
            parents=parents,
            policy=policy,
            available=available,
        )
        await self._save_node(node)

    async def _propagate(self) -> None:
        graph = await self._load_graph()
        # Sources already have authoritative availability; recompute descendants in topo order.
        order = _topo_order(graph)
        for node_id in order:
            node = graph.nodes[node_id]
            if node.kind == "source":
                continue
            assert node.policy is not None
            available = _compute_availability(graph, node.parents, node.policy)
            if available != node.available:
                node.available = available
                await self._save_node(node)
                if node.kind == "memory":
                    await self._sync_memory_state(node_id, graph=graph)
                else:
                    await self._sync_topic_state(node_id, graph=graph)
            else:
                # Still sync on first apply after restart for consistency.
                if node.kind == "memory":
                    await self._sync_memory_state(node_id, graph=graph)
                else:
                    await self._sync_topic_state(node_id, graph=graph)

    async def _sync_memory_state(self, node_id: str, *, graph: _ScopeGraph | None = None) -> None:
        graph = graph or await self._load_graph()
        node = graph.nodes[node_id]
        assert isinstance(node.reference, MemoryCitation)
        citation = node.reference
        memory = self._runtime.memory.for_scope(self.scope_id)
        # Historical citations keep their original Memory revision for audit reads.
        # State transitions must target the current head while preserving entry identity.
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                head_citation = MemoryCitation(
                    memory_ref=current.as_ref(),
                    entry_id=citation.entry_id,
                    entry_version_id=citation.entry_version_id,
                )
                record = await memory.get(GetMemoryEntryRequest(citation=head_citation))
                if node.available and record.state == "inactive":
                    await service.reactivate(
                        current, entries=(record.entry,), reason="source-lifecycle-restore"
                    )
                elif not node.available and record.state == "active":
                    await service.forget(
                        current, entries=(record.entry,), reason="source-lifecycle-withdraw"
                    )

    async def _sync_topic_state(self, node_id: str, *, graph: _ScopeGraph | None = None) -> None:
        graph = graph or await self._load_graph()
        node = graph.nodes[node_id]
        assert isinstance(node.reference, ArtifactRef)
        reference = node.reference
        # Use deprecated (reversible) rather than retired so restore remains possible.
        # Topic search filters ARTIFACT_HEADS.lifecycle_state so DEPRECATED topics
        # leave native search/Context while exact revisions stay readable.
        target = ArtifactLifecycleState.ACTIVE if node.available else ArtifactLifecycleState.DEPRECATED
        async with self._runtime._provider.database.transaction() as connection:
            from powercontext.builtin.persistence.artifact_governance import ArtifactGovernanceRepository

            repo = ArtifactGovernanceRepository()
            current = await repo.get(connection, self.scope_id, reference.family, reference.artifact_id)
            if current.lifecycle_state != target:
                await repo.transition(
                    connection,
                    self.scope_id,
                    reference.family,
                    reference.artifact_id,
                    current.governance_generation,
                    target,
                    None,
                )

    async def _ensure_schema(self) -> None:
        async with self._runtime._provider.database.transaction() as connection:
            await connection.run_sync(_METADATA.create_all)

    async def _load_graph(self) -> _ScopeGraph:
        await self._ensure_schema()
        async with self._runtime._provider.database.transaction() as connection:
            rows = (
                await connection.execute(select(_LIFECYCLE_NODES).where(_LIFECYCLE_NODES.c.scope_id == self.scope_id))
            ).mappings().all()
        graph = _ScopeGraph()
        for row in rows:
            graph.nodes[row["node_id"]] = _decode_node(row)
        return graph

    async def _load_node(self, node_id: str) -> _Node | None:
        graph = await self._load_graph()
        return graph.nodes.get(node_id)

    async def _save_node(self, node: _Node) -> None:
        await self._ensure_schema()
        payload = _encode_node(self.scope_id, node)
        async with self._runtime._provider.database.transaction() as connection:
            statement = sqlite_insert(_LIFECYCLE_NODES).values(**payload)
            statement = statement.on_conflict_do_update(
                index_elements=["scope_id", "node_id"],
                set_={key: payload[key] for key in payload if key not in {"scope_id", "node_id"}},
            )
            await connection.execute(statement)


class SourceLifecycleApplication:
    """Select the lifecycle service for one Scope."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> SourceLifecycleService:
        return SourceLifecycleService(self._runtime, scope_id)


def _require_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node_id must be a non-empty non-blank string")


def _compute_availability(graph: _ScopeGraph, parents: tuple[str, ...], policy: Policy) -> bool:
    support = [graph.nodes[parent].available for parent in parents]
    return all(support) if policy == "all" else any(support)


def _introduces_cycle(graph: _ScopeGraph, node_id: str, parents: tuple[str, ...]) -> bool:
    stack = list(parents)
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current == node_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        parent_node = graph.nodes.get(current)
        if parent_node is not None:
            stack.extend(parent_node.parents)
    return False


def _topo_order(graph: _ScopeGraph) -> list[str]:
    remaining = {node_id: set(node.parents) for node_id, node in graph.nodes.items()}
    ready = [node_id for node_id, parents in remaining.items() if not parents]
    ordered: list[str] = []
    while ready:
        node_id = ready.pop()
        ordered.append(node_id)
        for child, parents in remaining.items():
            if node_id in parents:
                parents.remove(node_id)
                if not parents and child not in ordered and child not in ready:
                    ready.append(child)
    if len(ordered) != len(graph.nodes):
        # Fall back to registration order for residual cycles (should be rejected earlier).
        ordered.extend(node_id for node_id in graph.nodes if node_id not in ordered)
    return ordered


def _encode_node(scope_id: str, node: _Node) -> dict[str, Any]:
    if isinstance(node.reference, SourceRef):
        reference_json = json.dumps({"type": "source", "value": node.reference.model_dump(mode="json")})
    elif isinstance(node.reference, MemoryCitation):
        reference_json = json.dumps({"type": "memory", "value": node.reference.model_dump(mode="json")})
    else:
        reference_json = json.dumps({"type": "topic", "value": node.reference.model_dump(mode="json")})
    return {
        "scope_id": scope_id,
        "node_id": node.node_id,
        "kind": node.kind,
        "policy": node.policy,
        "parents_json": json.dumps(list(node.parents)),
        "reference_json": reference_json,
        "revision": node.revision,
        "sequence": node.sequence,
        "available": 1 if node.available else 0,
    }


def _decode_node(row: Any) -> _Node:
    payload = json.loads(row["reference_json"])
    kind = row["kind"]
    if payload["type"] == "source":
        reference: SourceRef | MemoryCitation | ArtifactRef = SourceRef.model_validate(payload["value"])
    elif payload["type"] == "memory":
        reference = MemoryCitation.model_validate(payload["value"])
    else:
        reference = ArtifactRef.model_validate(payload["value"])
    parents = tuple(json.loads(row["parents_json"] or "[]"))
    return _Node(
        node_id=row["node_id"],
        kind=kind,
        reference=reference,
        parents=parents,
        policy=row["policy"],
        revision=row["revision"],
        sequence=int(row["sequence"] or 0),
        available=bool(row["available"]),
    )


__all__ = [
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleService",
]
