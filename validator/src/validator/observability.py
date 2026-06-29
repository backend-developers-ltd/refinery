"""Structured-logging sinks for the pipeline's result, failure, and error sources.

Refinery's observability surface is structured logging (the validator logs JSON via
``structlog``); it ships no ``/metrics`` endpoint — Nexus exposes no metrics registry
and the deployed stack already scrapes container, host, and Pylon metrics. Each log
line below carries metric-like fields (hotkey, validity, latency) for inspection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    ConsumerActor,
    Context,
    ContextStore,
    ExecutorFailureTaskResult,
    NexusException,
    Node,
    NodeSinks,
    NodeSources,
    PipeToBus,
    Sink,
    SinkName,
    SuccessfulTaskResult,
    WeightSettingSuccess,
    get_logger,
)

from validator.proof_of_work import PowChallenge, PowSolution, solution_is_valid

logger: logging.Logger = get_logger(__name__)


class SinkLoggerNode[T](Node, ActorBuilder):
    """Sink-only node that runs a logging callback for every message it receives.

    sink sink: messages of type T to be logged by the provided callback
    """

    sink: Sink[T]
    consume_fn: Callable[[Context, T], None]

    def __init__(self, _id: str, *, consume: Callable[[Context, T], None]) -> None:
        super().__init__(_id)
        self.sink = Sink(f"{self.id}-sink", owner_node=self)
        self.consume_fn = consume

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return SinkLoggerActor[T](
            sink=self.sink, consume=self.consume_fn, pipe_to_bus=pipe_to_bus, context_store=context_store
        )


class SinkLoggerActor[T](ConsumerActor[T]):
    """Actor that delegates each consumed message to its node's logging callback."""

    def __init__(
        self,
        *,
        sink: Sink[T],
        consume: Callable[[Context, T], None],
        pipe_to_bus: PipeToBus,
        context_store: ContextStore,
    ) -> None:
        super().__init__(sink, pipe_to_bus, context_store)
        self.consume_fn = consume

    @override
    def _consume(self, ctx: Context, payload: T) -> None:
        self.consume_fn(ctx, payload)


def log_solution(_ctx: Context, result: SuccessfulTaskResult[PowChallenge, PowSolution, PowSolution]) -> None:
    """Log a received miner solution with its validity and round-trip latency."""
    challenge = result.executor_payload
    solution = result.executor_output
    latency_s = (result.processing_finished - result.processing_started).total_seconds()
    logger.info(
        "pow.solution hotkey=%s uid=%s valid=%s latency_s=%.3f difficulty=%s block=%s",
        result.target.hotkey,
        result.target.uid,
        solution_is_valid(challenge.seed, solution.nonce, challenge.difficulty),
        latency_s,
        challenge.difficulty,
        result.block_at_finish.block_number,
    )


def log_failure(_ctx: Context, result: ExecutorFailureTaskResult[PowChallenge]) -> None:
    """Log a miner that failed to answer in time (e.g. timed out) for this challenge."""
    logger.warning(
        "pow.failure hotkey=%s uid=%s block=%s error=%r",
        result.target.hotkey,
        result.target.uid,
        result.block_at_finish.block_number,
        result.executor_failure,
    )


def log_pipeline_error(ctx: Context, error: NexusException) -> None:
    """Log a framework-side pipeline error from any connected error source."""
    logger.error("pipeline.error ctx=%s: %r", ctx.id, error, exc_info=error)


def log_weights_set(_ctx: Context, _result: WeightSettingSuccess) -> None:
    """Log a successful on-chain weight commit (per-miner detail is logged by the weighing function)."""
    logger.info("weights.set committed")


def discard(_ctx: Context, _payload: object) -> None:
    """Drain a source we intentionally don't consume, keeping the event bus quiet."""
