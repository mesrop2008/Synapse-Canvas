"""Runs each generation in a task of its own, outside any request, so a reader
that drops and reconnects finds the same generation rather than paying for a
second one.

The provider is iterated in a separate task that awaits nothing else. That
matters: cancelling a task closes Gemini's HTTP response only when it is
cancelled while awaiting the SDK (see api/llm/gemini.py). The runner stops
that task when the user cancels, when no reader has been seen for the grace
period, or when the generation overruns."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid

from redis.asyncio import Redis

from api.core.config import get_settings
from api.core.exceptions import ErrorCode
from api.db.session import get_sessionmaker
from api.llm.base import LLMProvider, LLMRequest, Usage, estimate_tokens
from api.llm.errors import ProviderError
from api.models.enums import AIQueryStatus
from api.services import ai_buffer, ai_queries
from api.services.ai_queries import Outcome

logger = logging.getLogger(__name__)

CONTROL_INTERVAL_SECONDS = 0.25
# Readers take a runner that has not refreshed this as dead.
RUNNER_TTL_MS = 5000
SHUTDOWN_WAIT_SECONDS = 5.0

_Item = str | Usage | Exception


async def _pump(
    provider: LLMProvider, request: LLMRequest, queue: asyncio.Queue[_Item]
) -> None:
    try:
        async for item in provider.stream(request):
            queue.put_nowait(item)
    except Exception as exc:
        queue.put_nowait(exc)


async def _stop(task: asyncio.Task[None]) -> None:
    """Returns once the task has unwound, so the upstream call is closed."""
    if not task.done():
        task.cancel()
        await asyncio.wait({task})


class GenerationRunner:
    def __init__(self, provider: LLMProvider, redis: Redis) -> None:
        self.provider = provider
        self._redis = redis
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self, query_id: uuid.UUID, request: LLMRequest) -> None:
        # The first reader gets the grace period to connect.
        await ai_buffer.touch_reader(self._redis, query_id)
        await ai_buffer.touch_runner(self._redis, query_id, RUNNER_TTL_MS)
        task = asyncio.create_task(
            self._run(query_id, request), name=f"ai-query:{query_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, query_id: uuid.UUID, request: LLMRequest) -> None:
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        pump = asyncio.create_task(_pump(self.provider, request, queue))
        produced: list[str] = []
        # Stands if this task is cancelled, which only shutdown does.
        outcome = Outcome(
            AIQueryStatus.FAILED,
            error_code=ErrorCode.AI_INTERRUPTED,
            detail="The server restarted during the generation",
        )
        try:
            outcome = await self._relay(query_id, queue, produced)
        except Exception:
            logger.exception("Generation %s failed", query_id)
            outcome = Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.AI_FAILED)
        finally:
            await _stop(pump)
            await asyncio.shield(self._finish(query_id, request, produced, outcome))

    async def _relay(
        self, query_id: uuid.UUID, queue: asyncio.Queue[_Item], produced: list[str]
    ) -> Outcome:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + get_settings().ai_query_timeout_seconds
        next_check = 0.0

        while True:
            now = loop.time()
            if now >= next_check:
                cancelled, reading = await ai_buffer.control(self._redis, query_id)
                if cancelled:
                    return Outcome(AIQueryStatus.CANCELLED)
                if not reading:
                    logger.info("Nobody is reading %s; cancelling it", query_id)
                    return Outcome(AIQueryStatus.CANCELLED)
                if now >= deadline:
                    return Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.AI_TIMEOUT)
                await ai_buffer.touch_runner(self._redis, query_id, RUNNER_TTL_MS)
                next_check = now + CONTROL_INTERVAL_SECONDS

            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=max(0.0, next_check - loop.time())
                )
            except TimeoutError:
                continue

            if isinstance(item, Usage):
                return Outcome(
                    AIQueryStatus.COMPLETED, response="".join(produced), usage=item
                )
            if isinstance(item, ProviderError):
                return Outcome(
                    AIQueryStatus.FAILED, error_code=item.code, detail=item.detail
                )
            if isinstance(item, Exception):
                logger.error("Provider raised for %s", query_id, exc_info=item)
                return Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.AI_FAILED)

            produced.append(item)
            await ai_buffer.append(self._redis, query_id, "token", {"text": item})

    def _estimated_usage(
        self, request: LLMRequest, produced: list[str], outcome: Outcome
    ) -> Usage | None:
        """Providers report usage only at the end, yet a stopped generation
        was still billed: the prompt once sent, and whatever came back."""
        if outcome.status is AIQueryStatus.FAILED and not produced:
            return None
        return Usage(
            prompt_tokens=estimate_tokens(request.system + request.user),
            completion_tokens=estimate_tokens("".join(produced)),
            model=getattr(self.provider, "model", self.provider.name),
        )

    async def _finish(
        self,
        query_id: uuid.UUID,
        request: LLMRequest,
        produced: list[str],
        outcome: Outcome,
    ) -> None:
        if outcome.usage is None:
            usage = self._estimated_usage(request, produced, outcome)
            outcome = dataclasses.replace(outcome, usage=usage)

        # The row first: a reader that sees `done` may apply at once.
        try:
            async with get_sessionmaker()() as db:
                recorded = await ai_queries.finish(db, query_id, outcome)
        except Exception:
            logger.exception("Could not record the end of %s", query_id)
            outcome = Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.SERVER_ERROR)
        else:
            if not recorded:
                outcome = Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.AI_TIMEOUT)

        try:
            await ai_buffer.append(self._redis, query_id, *_terminal_event(outcome))
            await ai_buffer.clear_runner(self._redis, query_id)
        except Exception:
            logger.exception("Could not publish the end of %s", query_id)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=SHUTDOWN_WAIT_SECONDS)
        await self.provider.aclose()


def _terminal_event(outcome: Outcome) -> tuple[ai_buffer.EventType, dict[str, object]]:
    if outcome.status is AIQueryStatus.COMPLETED:
        usage = outcome.usage
        return "done", {
            "response": outcome.response or "",
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "model": usage.model if usage else None,
            },
        }
    if outcome.status is AIQueryStatus.CANCELLED:
        return "cancelled", {}
    code = outcome.error_code or ErrorCode.AI_FAILED
    return "error", {"code": code.value, "detail": outcome.detail or code.value}
