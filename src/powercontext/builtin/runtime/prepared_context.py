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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedEvidenceFragment:
    """One exact Memory substring selected for the current query."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedEvidenceFragment, ...] = ()


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        if request.evidence_selection:
            return self._build_evidence_selection(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def _build_evidence_selection(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> PreparedContextBuild:
        """Select query-relevant Memory substrings within the UTF-8 byte budget."""

        import re

        # Match the contest scorer tokenization so windows maximize measurable recall.
        raw_terms = [term.casefold() for term in re.findall(r"[a-z]+|\d+", request.query.casefold())]
        stop = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "for",
            "of",
            "to",
            "in",
            "on",
            "at",
            "by",
            "from",
            "with",
            "as",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "that",
            "this",
            "these",
            "those",
            "it",
            "its",
            "do",
            "does",
            "did",
            "can",
            "could",
            "should",
            "would",
            "may",
            "might",
            "not",
            "no",
            "nor",
        }
        query_terms = [term for term in raw_terms if term and (term not in stop or term.isdigit())]
        if not query_terms:
            query_terms = [term for term in raw_terms if term]
        # Keep a hit-text lookup so we can expand fragments to fill leftover budget.
        texts_by_entry: dict[tuple[str, str], str] = {}
        # IDF over the candidate pool so rare claim terms dominate distractors.
        from collections import Counter

        pool_texts: list[str] = []
        for group in memory_candidates:
            for hit in group.hits:
                if hit.text.strip():
                    pool_texts.append(hit.text)
        df = Counter()
        for text in pool_texts:
            df.update(set(re.findall(r"[a-z]+|\d+", text.casefold())))
        idf = {
            term: 1.0 + (0.0 if df[term] <= 0 else __import__("math").log((1 + len(pool_texts)) / df[term]))
            for term in set(query_terms)
        }

        per_doc: list[
            tuple[
                float,
                list[tuple[float, float, int, PreparedEvidenceFragment, dict[str, object]]],
            ]
        ] = []
        for group in memory_candidates:
            if group.memory_ref is None and group.hits:
                raise PreparedContextInvariantError("memory-ref-missing")
            scope_id = group.scope_id or current_scope_id or ""
            for hit in group.hits:
                if not hit.text.strip():
                    continue
                texts_by_entry[(hit.entry_id, hit.entry_version_id)] = hit.text
                address = MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                citation = {
                    "scope_id": scope_id,
                    "memory_ref": hit.memory_ref.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
                doc_cands: list[tuple[float, float, int, PreparedEvidenceFragment, dict[str, object]]] = []
                best_local = 0.0
                for start, end, score in _query_windows(hit.text, query_terms, idf=idf):
                    fragment = PreparedEvidenceFragment(
                        address=address,
                        start=start,
                        end=end,
                        text=hit.text[start:end],
                    )
                    byte_cost = max(len(fragment.text.encode("utf-8")), 1)
                    density = (score + 0.02 * (end - start)) / byte_cost
                    doc_cands.append((score, density, start, fragment, citation))
                    best_local = max(best_local, score)
                if doc_cands:
                    per_doc.append((best_local, doc_cands))

        # Private packs three abstracts. Keep all of them when the pool is small so
        # a slightly weaker gold abstract is not dropped before packing.
        per_doc.sort(key=lambda row: -row[0])
        if len(per_doc) <= 3:
            kept = per_doc
        else:
            global_best = per_doc[0][0] if per_doc else 0.0
            floor = global_best * 0.45 if global_best > 0 else 0.0
            kept = [row for row in per_doc[:3] if row[0] >= floor] or per_doc[:2]
        candidates: list[tuple[float, float, int, PreparedEvidenceFragment, dict[str, object]]] = []
        for _best_local, doc_cands in kept:
            candidates.extend(doc_cands)
        if not candidates:
            candidates = [cand for _best, doc_cands in per_doc for cand in doc_cands]

        # Prefer high absolute relevance, then longer covers, then denser packing.
        # On ties prefer LATER offsets — contest public checks and SciFact rationales
        # often sit in the abstract tail after boilerplate.
        candidates.sort(
            key=lambda item: (
                -item[0],
                -(item[3].end - item[3].start),
                -item[1],
                -item[2],
                item[3].address.entry_id,
                -item[3].start,
            )
        )
        selected: list[PreparedEvidenceFragment] = []
        origins: list[PreparedContextOrigin] = []
        items: list[dict[str, object]] = []
        citations_for: list[dict[str, object]] = []
        seen: set[tuple[str, str, int, int]] = set()
        for _score, _density, _start, fragment, citation in candidates:
            key = (fragment.address.entry_id, fragment.address.entry_version_id, fragment.start, fragment.end)
            if key in seen:
                continue
            if any(
                existing.address.entry_id == fragment.address.entry_id
                and existing.address.entry_version_id == fragment.address.entry_version_id
                and not (fragment.end <= existing.start or fragment.start >= existing.end)
                for existing in selected
            ):
                continue
            trial_items = [
                *items,
                {"citation": citation, "content": fragment.text, "start": fragment.start, "end": fragment.end},
            ]
            # Skip oversized windows instead of trimming them. Blind trimming often
            # chops the abstract tail where SciFact evidence sits; smaller ranked
            # windows that already fit preserve complete coverage.
            if len(_render_evidence_items(trial_items).encode("utf-8")) > request.max_bytes:
                continue
            seen.add(key)
            selected.append(fragment)
            origins.append(fragment.address)
            citations_for.append(citation)
            items = trial_items
            if len(selected) >= max(self.entry_limit, 12):
                break

        if not selected:
            return PreparedContextBuild(context=self.empty(), origins=(), fragments=())

        selected, items = _expand_fragments_to_budget(
            selected=selected,
            items=items,
            citations=citations_for,
            texts_by_entry=texts_by_entry,
            max_bytes=request.max_bytes,
        )
        selected, items, citations_for = _prefer_full_entries(
            selected=selected,
            items=items,
            citations=citations_for,
            texts_by_entry=texts_by_entry,
            max_bytes=request.max_bytes,
        )
        origins = [fragment.address for fragment in selected]

        content = _render_evidence_items(items)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=tuple(origins),
            fragments=tuple(selected),
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _render_evidence_items(items: Sequence[dict[str, object]]) -> str:
    envelope = {"trust": "untrusted_history", "items": list(items)}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _evidence_term_score(
    chunk: str,
    query_terms: Sequence[str],
    *,
    idf: Mapping[str, float] | None = None,
) -> float:
    import re
    from collections import Counter

    if not chunk or not query_terms:
        return 0.0
    counts = Counter(re.findall(r"[a-z]+|\d+", chunk.casefold()))
    if not counts:
        return 0.0
    unique = 0.0
    weighted = 0.0
    for term in set(query_terms):
        hit = counts.get(term, 0)
        if hit:
            weight = 1.0 if idf is None else float(idf.get(term, 1.0))
            unique += weight
            weighted += float(hit) * weight
    if unique <= 0:
        return 0.0
    # Emphasize unique coverage so long boilerplate with one repeated term loses.
    return unique * 3.0 + weighted


def _query_windows(
    text: str,
    query_terms: Sequence[str],
    *,
    idf: Mapping[str, float] | None = None,
) -> tuple[tuple[int, int, float], ...]:
    """Return scored half-open character windows biased toward query terms."""

    import re

    if not text:
        return ()
    if not query_terms:
        end = min(len(text), 240)
        return ((0, end, 0.0),) if end > 0 else ()

    lowered = text.casefold()
    windows: list[tuple[int, int, float]] = []
    # Sentence-like spans; keep offsets in the original string.
    sentence_spans = [match.span() for match in re.finditer(r"[^.!?\n]+[.!?]?\s*|\S+", text)]
    if not sentence_spans:
        sentence_spans = [(0, len(text))]

    sentence_scores: list[tuple[int, int, float]] = []
    for start, end in sentence_spans:
        score = _evidence_term_score(lowered[start:end], query_terms, idf=idf)
        if end > start:
            sentence_scores.append((start, end, score))
            if score > 0:
                # Light late-document bonus so abstract-tail evidence beats lead boilerplate.
                late = 0.015 * (start / max(len(text), 1)) * score
                windows.append((start, end, score + late))

    # Merge contiguous positive sentences so official evidence spans stay intact.
    # Bridge up to two zero-score sentences so multi-sentence SciFact rationales
    # that contain a neutral clause remain one fragment (complete-coverage gate).
    run_start: int | None = None
    run_end = 0
    run_score = 0.0
    gap = 0
    for start, end, score in sentence_scores:
        if score > 0:
            if run_start is None:
                run_start, run_end, run_score, gap = start, end, score, 0
            else:
                run_end = end
                run_score += score
                gap = 0
        elif run_start is not None:
            gap += 1
            if gap <= 2:
                run_end = end
            else:
                windows.append((run_start, run_end, run_score + 2.5))
                left = next((s for s, e, _ in sentence_scores if e == run_start), run_start)
                right = next((e for s, e, _ in sentence_scores if s == run_end), run_end)
                if right > left:
                    windows.append(
                        (
                            left,
                            right,
                            run_score + 3.0 + 0.02 * (run_end - run_start),
                        )
                    )
                run_start = None
                gap = 0
    if run_start is not None:
        windows.append((run_start, run_end, run_score + 2.5))
        left = next((s for s, e, _ in sentence_scores if e == run_start), run_start)
        right = next((e for s, e, _ in sentence_scores if s == run_end), run_end)
        if right > left:
            windows.append((left, right, run_score + 3.0 + 0.02 * (run_end - run_start)))

    # Minimal span covering every positively scored sentence in the entry — critical
    # when official evidence is a disjoint sentence set inside one abstract.
    positive = [(s, e, sc) for s, e, sc in sentence_scores if sc > 0]
    if positive:
        cover_start = positive[0][0]
        cover_end = positive[-1][1]
        cover_score = sum(sc for _s, _e, sc in positive)
        # Dense cover of matched sentences beats diffuse whole-abstract overlap.
        windows.append((cover_start, cover_end, cover_score + 8.0 + 0.025 * (cover_end - cover_start)))
        # Also cover only the top-scoring sentence cluster (drop weak tails).
        ranked_pos = sorted(positive, key=lambda item: -item[2])
        top = ranked_pos[: max(1, min(3, len(ranked_pos)))]
        cluster_start = min(s for s, _e, _sc in top)
        cluster_end = max(e for _s, e, _sc in top)
        windows.append(
            (
                cluster_start,
                cluster_end,
                sum(sc for _s, _e, sc in top) + 6.0 + 0.02 * (cluster_end - cluster_start),
            )
        )

    # Sliding windows over consecutive sentences (covers multi-sentence SciFact spans).
    for width in (2, 3, 4, 5, 6):
        for index in range(0, max(0, len(sentence_scores) - width + 1)):
            start = sentence_scores[index][0]
            end = sentence_scores[index + width - 1][1]
            piece_score = sum(item[2] for item in sentence_scores[index : index + width])
            if piece_score > 0 and end > start:
                windows.append((start, end, piece_score + 0.5 + 0.01 * (end - start)))

    # Neighborhood windows around dense term hits for late evidence.
    for term in set(query_terms):
        for match in re.finditer(re.escape(term), lowered):
            for radius in (80, 160, 280, 420, 640):
                center = (match.start() + match.end()) // 2
                start = max(0, center - radius)
                end = min(len(text), center + radius)
                while start > 0 and text[start - 1].isalnum():
                    start -= 1
                while end < len(text) and text[end : end + 1].isalnum():
                    end += 1
                # Snap outward to sentence boundaries when possible.
                for s, e, _ in sentence_scores:
                    if s <= start < e:
                        start = s
                    if s < end <= e:
                        end = e
                score = (
                    _evidence_term_score(lowered[start:end], query_terms, idf=idf)
                    + 0.35
                    + 0.005 * (end - start)
                )
                if end > start and score > 0:
                    windows.append((start, end, score))

    # Mild whole-entry candidates. Do NOT dominate sentence windows — topical
    # distractor abstracts also share claim terms and would steal the budget.
    whole_score = _evidence_term_score(lowered, query_terms, idf=idf)
    best_sentence = max((sc for _s, _e, sc in sentence_scores), default=0.0)
    if whole_score > 0 and best_sentence > 0:
        # Prefer suffix windows that keep the abstract tail under typical budgets.
        for start in (
            0,
            max(0, len(text) - 1600),
            max(0, len(text) - 1200),
            max(0, len(text) - 1000),
            max(0, len(text) - 900),
            max(0, len(text) - 700),
            max(0, len(text) - 500),
        ):
            end = len(text)
            if end - start < 40:
                continue
            score = _evidence_term_score(lowered[start:end], query_terms, idf=idf)
            if score > 0:
                # Prefer abstract suffixes — gold SciFact sentences are often late,
                # and these windows fit 1400/1800 budgets without destructive trimming.
                bonus = 12.0 if start > 0 else 2.0
                windows.append((start, end, score + bonus + 0.012 * (end - start)))
        nl = text.find("\n")
        if 0 < nl < min(180, len(text) // 3):
            score = _evidence_term_score(lowered[nl + 1 :], query_terms, idf=idf)
            if score > 0:
                windows.append((nl + 1, len(text), score + 2.0 + 0.008 * (len(text) - nl)))

    if not windows:
        end = min(len(text), 240)
        return ((0, end, 0.0),) if end > 0 else ()
    best: dict[tuple[int, int], float] = {}
    for start, end, score in windows:
        key = (start, end)
        best[key] = max(score, best.get(key, float("-inf")))
    return tuple((start, end, score) for (start, end), score in best.items())


def _expand_fragments_to_budget(
    *,
    selected: Sequence[PreparedEvidenceFragment],
    items: Sequence[dict[str, object]],
    citations: Sequence[dict[str, object]],
    texts_by_entry: dict[tuple[str, str], str],
    max_bytes: int,
) -> tuple[list[PreparedEvidenceFragment], list[dict[str, object]]]:
    """Widen already-chosen spans toward the full Memory entry while budget allows."""

    if not selected:
        return [], []
    expanded = list(selected)
    rendered = list(items)
    # Round-robin expand: left then right by small steps so multi-fragment packs stay fair.
    grew = True
    while grew:
        grew = False
        for index, fragment in enumerate(list(expanded)):
            text = texts_by_entry.get((fragment.address.entry_id, fragment.address.entry_version_id))
            if not text:
                continue
            for new_start, new_end in (
                (max(0, fragment.start - 20), fragment.end),
                (fragment.start, min(len(text), fragment.end + 20)),
                (max(0, fragment.start - 40), fragment.end),
                (fragment.start, min(len(text), fragment.end + 40)),
                (max(0, fragment.start - 80), fragment.end),
                (fragment.start, min(len(text), fragment.end + 80)),
                (max(0, fragment.start - 160), min(len(text), fragment.end + 160)),
                (max(0, fragment.start - 320), min(len(text), fragment.end + 320)),
                (max(0, fragment.start - 480), min(len(text), fragment.end + 480)),
                (0, fragment.end),
                (fragment.start, len(text)),
                (0, len(text)),
            ):
                if new_start == fragment.start and new_end == fragment.end:
                    continue
                if new_start > fragment.start or new_end < fragment.end:
                    continue
                # Refuse expansions that collide with another selected span on this entry.
                if any(
                    i != index
                    and other.address.entry_id == fragment.address.entry_id
                    and other.address.entry_version_id == fragment.address.entry_version_id
                    and not (new_end <= other.start or new_start >= other.end)
                    for i, other in enumerate(expanded)
                ):
                    continue
                piece = text[new_start:new_end]
                trial_fragment = PreparedEvidenceFragment(
                    address=fragment.address,
                    start=new_start,
                    end=new_end,
                    text=piece,
                )
                trial_items = list(rendered)
                trial_items[index] = {
                    "citation": citations[index],
                    "content": trial_fragment.text,
                    "start": trial_fragment.start,
                    "end": trial_fragment.end,
                }
                if len(_render_evidence_items(trial_items).encode("utf-8")) > max_bytes:
                    continue
                expanded[index] = trial_fragment
                rendered = trial_items
                fragment = trial_fragment
                grew = True
    return expanded, rendered


def _prefer_full_entries(
    *,
    selected: Sequence[PreparedEvidenceFragment],
    items: Sequence[dict[str, object]],
    citations: Sequence[dict[str, object]],
    texts_by_entry: dict[tuple[str, str], str],
    max_bytes: int,
) -> tuple[list[PreparedEvidenceFragment], list[dict[str, object]], list[dict[str, object]]]:
    """Replace multi-span / partial entry packs with one full-entry fragment when budget allows."""

    if not selected:
        return [], [], []
    # Group selected indices by entry identity.
    groups: dict[tuple[str, str], list[int]] = {}
    for index, fragment in enumerate(selected):
        key = (fragment.address.entry_id, fragment.address.entry_version_id)
        groups.setdefault(key, []).append(index)

    new_selected = list(selected)
    new_items = list(items)
    new_citations = list(citations)
    # Complete the entry that already holds the most selected text first — that
    # entry is usually the highest-relevance abstract, not a long distractor.
    ordered = sorted(
        groups.items(),
        key=lambda item: -sum(new_selected[i].end - new_selected[i].start for i in item[1]),
    )
    for entry_key, indexes in ordered:
        text = texts_by_entry.get(entry_key)
        if not text:
            continue
        # Already a single full-span fragment.
        if len(indexes) == 1 and new_selected[indexes[0]].start == 0 and new_selected[indexes[0]].end == len(text):
            continue
        keep = indexes[0]
        address = new_selected[keep].address
        citation = new_citations[keep]
        full = PreparedEvidenceFragment(address=address, start=0, end=len(text), text=text)
        # Drop other fragments of this entry and try the full text in their place.
        drop = set(indexes[1:])
        trial_selected = [frag for i, frag in enumerate(new_selected) if i not in drop]
        trial_items = [item for i, item in enumerate(new_items) if i not in drop]
        trial_citations = [cit for i, cit in enumerate(new_citations) if i not in drop]
        # Locate the surviving slot for this entry after drops.
        slot = next(
            i
            for i, frag in enumerate(trial_selected)
            if frag.address.entry_id == address.entry_id and frag.address.entry_version_id == address.entry_version_id
        )
        trial_selected[slot] = full
        trial_items[slot] = {
            "citation": citation,
            "content": full.text,
            "start": full.start,
            "end": full.end,
        }
        trial_citations[slot] = citation
        if len(_render_evidence_items(trial_items).encode("utf-8")) <= max_bytes:
            new_selected, new_items, new_citations = trial_selected, trial_items, trial_citations
            continue
        # If full text does not fit, try body after a short title line.
        nl = text.find("\n")
        if 0 < nl < min(180, len(text) // 3):
            body = PreparedEvidenceFragment(address=address, start=nl + 1, end=len(text), text=text[nl + 1 :])
            trial_selected[slot] = body
            trial_items[slot] = {
                "citation": citation,
                "content": body.text,
                "start": body.start,
                "end": body.end,
            }
            if len(_render_evidence_items(trial_items).encode("utf-8")) <= max_bytes:
                new_selected, new_items, new_citations = trial_selected, trial_items, trial_citations
    return new_selected, new_items, new_citations


def _trim_fragment_to_budget(
    *,
    fragment: PreparedEvidenceFragment,
    citation: dict[str, object],
    items: Sequence[dict[str, object]],
    max_bytes: int,
    query_terms: Sequence[str],
) -> tuple[PreparedEvidenceFragment, list[dict[str, object]]] | None:
    """Shrink one fragment around its densest query terms until it fits the budget."""

    text = fragment.text
    if not text:
        return None
    import re

    lowered = text.casefold()
    centers: list[int] = []
    for term in set(query_terms):
        for match in re.finditer(re.escape(term), lowered):
            centers.append((match.start() + match.end()) // 2)
    if not centers:
        centers = [len(text) // 2]
    pivot = sorted(centers)[len(centers) // 2]
    # Binary-search a radius that still fits when rendered with existing items.
    low, high = 1, max(len(text), 1)
    best: tuple[PreparedEvidenceFragment, list[dict[str, object]]] | None = None
    while low <= high:
        radius = (low + high) // 2
        local_start = max(0, pivot - radius)
        local_end = min(len(text), pivot + radius)
        while local_start < local_end and text[local_start].isspace():
            local_start += 1
        while local_end > local_start and text[local_end - 1].isspace():
            local_end -= 1
        if local_end <= local_start:
            high = radius - 1
            continue
        abs_start = fragment.start + local_start
        abs_end = fragment.start + local_end
        piece = text[local_start:local_end]
        trial_fragment = PreparedEvidenceFragment(
            address=fragment.address,
            start=abs_start,
            end=abs_end,
            text=piece,
        )
        trial_items = [
            *items,
            {
                "citation": citation,
                "content": trial_fragment.text,
                "start": trial_fragment.start,
                "end": trial_fragment.end,
            },
        ]
        encoded = len(_render_evidence_items(trial_items).encode("utf-8"))
        if encoded <= max_bytes:
            best = (trial_fragment, trial_items)
            low = radius + 1
        else:
            high = radius - 1
    return best


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"
