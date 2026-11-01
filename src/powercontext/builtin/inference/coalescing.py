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

"""Cross-call embedding request coalescing with a bounded LRU vector cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel, embed_query


@dataclass
class _InFlight:
    text: str
    future: asyncio.Future[tuple[EmbeddingVector, InferenceUsage]]
    waiters: int = 0


@dataclass
class _CallState:
    texts: tuple[str, ...]
    vectors: list[EmbeddingVector | None] = field(default_factory=list)
    pending: set[str] = field(default_factory=set)
    usages: list[InferenceUsage] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None


class CoalescingEmbeddingModel:
    """Merge concurrent embedding requests and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if type(cache_size) is not int or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_ms = float(coalesce_ms)
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, _InFlight] = {}
        self._queue: list[str] = []
        self._queued: set[str] = set()
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    async def embed(self, texts: tuple[str, ...] | Sequence[str], /) -> EmbeddingResult:
        normalized = tuple(texts)
        if not normalized:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        state = _CallState(texts=normalized, vectors=[None] * len(normalized))
        waiters: list[asyncio.Future[tuple[EmbeddingVector, InferenceUsage]]] = []

        async with self._lock:
            for index, text in enumerate(normalized):
                cached = self._cache_get(text)
                if cached is not None:
                    state.vectors[index] = cached
                    continue
                inflight = self._inflight.get(text)
                if inflight is None:
                    loop = asyncio.get_running_loop()
                    future: asyncio.Future[tuple[EmbeddingVector, InferenceUsage]] = loop.create_future()
                    inflight = _InFlight(text=text, future=future)
                    self._inflight[text] = inflight
                    if text not in self._queued:
                        self._queue.append(text)
                        self._queued.add(text)
                    self._ensure_worker()
                    self._schedule_flush()
                inflight.waiters += 1
                state.pending.add(text)
                waiters.append(inflight.future)

                def _on_done(
                    done: asyncio.Future[tuple[EmbeddingVector, InferenceUsage]],
                    *,
                    call=state,
                    position=index,
                    key=text,
                ) -> None:
                    try:
                        vector, usage = done.result()
                    except BaseException as error:  # noqa: BLE001 - propagate to the call state
                        if call.error is None:
                            call.error = error
                    else:
                        call.vectors[position] = vector
                        call.usages.append(usage)
                    call.pending.discard(key)
                    if not call.pending:
                        call.done.set()

                inflight.future.add_done_callback(_on_done)

            if not state.pending:
                return EmbeddingResult(vectors=tuple(state.vectors), usage=InferenceUsage(requests=0))  # type: ignore[arg-type]

        try:
            await state.done.wait()
        except asyncio.CancelledError:
            async with self._lock:
                for text in list(state.pending):
                    inflight = self._inflight.get(text)
                    if inflight is not None:
                        inflight.waiters = max(0, inflight.waiters - 1)
            raise

        if state.error is not None:
            raise state.error
        usage = _merge_usage(state.usages)
        return EmbeddingResult(vectors=tuple(state.vectors), usage=usage)  # type: ignore[arg-type]

    async def embed_query(self, texts: tuple[str, ...] | Sequence[str], /) -> EmbeddingResult:
        # Query path shares the same coalescing/cache surface when the base model
        # distinguishes queries; otherwise fall back to document embeddings.
        if isinstance(self._model, QueryEmbeddingModel):
            return await self.embed(texts)
        return await embed_query(self._model, tuple(texts))

    def _cache_get(self, text: str) -> EmbeddingVector | None:
        if self._cache_size <= 0 or text not in self._cache:
            return None
        self._cache.move_to_end(text)
        return self._cache[text]

    def _cache_put(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(), name="coalescing-embedding-worker")

    def _schedule_flush(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        # Even coalesce_ms=0 yields once so concurrent callers can enqueue first.
        delay = 0.0 if self._coalesce_ms <= 0 else self._coalesce_ms / 1000.0
        await asyncio.sleep(delay)
        self._wake.set()

    async def _run_worker(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                batch = await self._take_batch()
                if not batch:
                    async with self._lock:
                        if self._queue:
                            continue
                    break
                await self._embed_batch(batch)
                async with self._lock:
                    if self._queue and (self._flush_task is None or self._flush_task.done()):
                        self._schedule_flush()

    async def _take_batch(self) -> list[str]:
        async with self._lock:
            if not self._queue:
                return []
            batch: list[str] = []
            while self._queue and len(batch) < self._max_batch_size:
                text = self._queue.pop(0)
                self._queued.discard(text)
                if text in self._inflight and not self._inflight[text].future.done():
                    batch.append(text)
            return batch

    async def _embed_batch(self, batch: list[str]) -> None:
        try:
            result = await asyncio.shield(self._model.embed(tuple(batch)))
            if len(result.vectors) != len(batch):
                raise ValueError("embedding vector count does not match batch size")
            usage = result.usage
            async with self._lock:
                for text, vector in zip(batch, result.vectors, strict=True):
                    self._cache_put(text, vector)
                    inflight = self._inflight.pop(text, None)
                    if inflight is not None and not inflight.future.done():
                        inflight.future.set_result((vector, usage))
        except BaseException as error:  # noqa: BLE001 - fan out provider/local failures
            async with self._lock:
                for text in batch:
                    inflight = self._inflight.pop(text, None)
                    if inflight is not None and not inflight.future.done():
                        inflight.future.set_exception(error)


def _merge_usage(usages: list[InferenceUsage]) -> InferenceUsage:
    if not usages:
        return InferenceUsage(requests=0)
    # One call may wait on several batches; each waited batch is counted once.
    # Identical usage objects from the same batch are de-duplicated by identity
    # of the provider batch through request aggregation of unique wait events.
    requests = 0
    input_tokens = 0
    output_tokens = 0
    saw_input = False
    saw_output = False
    seen: set[int] = set()
    for usage in usages:
        marker = id(usage)
        if marker in seen:
            continue
        seen.add(marker)
        requests += usage.requests
        if usage.input_tokens is not None:
            saw_input = True
            input_tokens += usage.input_tokens
        if usage.output_tokens is not None:
            saw_output = True
            output_tokens += usage.output_tokens
    return InferenceUsage(
        requests=requests,
        input_tokens=input_tokens if saw_input else None,
        output_tokens=output_tokens if saw_output else None,
    )


__all__ = ["CoalescingEmbeddingModel"]
