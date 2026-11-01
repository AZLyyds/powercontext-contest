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

"""HTTP scoring-service adapter for Memory listwise reranking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score Memory candidates through a batched HTTP ranking endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            payload = {
                "query": query,
                "candidates": [
                    {"rank": start + offset + 1, "text": candidate.text}
                    for offset, candidate in enumerate(batch)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            try:
                body = response.json()
            except ValueError as exc:
                raise ValueError("reranker response must be valid JSON") from exc
            expected_ranks = {start + offset + 1 for offset in range(len(batch))}
            batch_scores = _parse_scores(body, expected_ranks)
            scores.update(batch_scores)

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        selected = tuple(rank for rank, _ in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(body: object, expected_ranks: set[int]) -> dict[int, float]:
    if not isinstance(body, Mapping) or set(body) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")
    scores_value = body["scores"]
    if not isinstance(scores_value, Sequence) or isinstance(scores_value, (str, bytes)):
        raise ValueError("reranker scores must be a list")
    parsed: dict[int, float] = {}
    for item in scores_value:
        if not isinstance(item, Mapping) or set(item) != {"rank", "score"}:
            raise ValueError("each score must contain exactly rank and score")
        rank = item["rank"]
        score = item["score"]
        if type(rank) is not int or isinstance(rank, bool):
            raise ValueError("score rank must be an integer")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("score must be a finite number")
        score = float(score)
        if not math.isfinite(score):
            raise ValueError("score must be a finite number")
        if rank not in expected_ranks:
            raise ValueError("score rank is outside the current batch")
        if rank in parsed:
            raise ValueError("duplicate score rank in batch response")
        parsed[rank] = score
    if set(parsed) != expected_ranks:
        raise ValueError("batch response must cover every requested rank exactly once")
    return parsed


__all__ = ["HTTPMemoryReranker"]
