"""Refinery validator: each block, challenge a miner with a proof-of-work task; each epoch, weigh.

Pipeline: subnet clock (BlockBeat) -> PoW challenge -> miner router -> HTTP communicator,
with successful solutions and timeouts persisted in the task result store. A separate epoch
clock drives the weight setter, whose weighing function verifies the epoch's stored solutions
and converts per-miner correctness and speed into on-chain weights.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from ipaddress import IPv4Address
from pathlib import Path

import click
from dotenv import load_dotenv
from nexus.v1 import (
    AsyncHttpNeuronCommunicator,
    BlockBeat,
    BlockCount,
    EpochBeatNode,
    ExecutorFailureTaskResult,
    NetUid,
    NexusException,
    NexusTask,
    NexusTaskName,
    NexusValidator,
    NoopPayloadCreator,
    Port,
    RetryStrategy,
    RoundRobinNeuronRouter,
    SuccessfulTaskResult,
    WeightSetterNode,
    WeightSettingSuccess,
)
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from validator.logging_config import LoggingSettings, configure_logging
from validator.observability import (
    SinkLoggerNode,
    discard,
    log_failure,
    log_pipeline_error,
    log_solution,
    log_weights_set,
)
from validator.otel import OtelSettings, setup_otel
from validator.payload import PowChallengePayloadCreator
from validator.proof_of_work import PowChallenge, PowSolution
from validator.routing import servable_http_miners
from validator.weighing import PowWeighing

TASK_NAME = NexusTaskName("pow_challenge")


class Settings(BaseSettings):
    """Runtime configuration for the Refinery validator (all knobs are ``VALIDATOR_*`` env vars)."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_", extra="ignore")

    netuid: int = Field(validation_alias=AliasChoices("VALIDATOR_NETUID", "NETUID"))
    callback_host: str = "127.0.0.1"
    callback_port: int = 8001

    difficulty: int = 16
    challenge_deadline: timedelta = timedelta(seconds=10)
    send_timeout: timedelta = timedelta(seconds=2)
    max_in_flight: int = 16
    max_attempts: int = 1

    speed_weight: float = 0.25
    target_latency: timedelta = timedelta(seconds=2)
    weight_set_delay_blocks: int = 0


class Validator(NexusValidator):
    """Refinery validator wiring: subnet clock -> PoW task -> result store -> epoch weight setter."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

        payload_creator = PowChallengePayloadCreator("pow-challenge-creator", difficulty=settings.difficulty)
        router = RoundRobinNeuronRouter[PowChallenge](
            "miner-router", netuid=settings.netuid, neuron_filter=servable_http_miners
        )
        communicator = AsyncHttpNeuronCommunicator[PowChallenge, PowSolution](
            "miner-communicator",
            target_path="/task",
            send_timeout=settings.send_timeout,
            total_processing_timeout=settings.challenge_deadline,
            max_in_flight=settings.max_in_flight,
            callback_bind_ip=IPv4Address("0.0.0.0"),
            callback_port=Port(settings.callback_port),
            callback_path="/callback",
            callback_base_url=f"http://{settings.callback_host}:{settings.callback_port}",
            input_model=PowChallenge,
            output_model=PowSolution,
        )
        retry = RetryStrategy[BlockBeat]("pow-retry", settings.max_attempts, timedelta(seconds=0))
        converter = NoopPayloadCreator[PowSolution]("solution-passthrough")
        task = NexusTask[BlockBeat, PowChallenge, PowSolution, PowSolution](
            name=TASK_NAME,
            retry=retry,
            payload_creator=payload_creator,
            router=router,
            executor_communicator=communicator,
            executor_result_converter=converter,
        )

        epoch_clock = EpochBeatNode(
            "epoch-clock",
            netuid=NetUid(settings.netuid),
            delay=BlockCount(settings.weight_set_delay_blocks),
        )
        weighing = PowWeighing(
            task_name=TASK_NAME,
            speed_weight=settings.speed_weight,
            target_latency_s=settings.target_latency.total_seconds(),
        )
        weight_setter = WeightSetterNode("weight-setter", weighing_func=weighing)

        solution_logger = SinkLoggerNode[SuccessfulTaskResult[PowChallenge, PowSolution, PowSolution]](
            "solution-logger", consume=log_solution
        )
        failure_logger = SinkLoggerNode[ExecutorFailureTaskResult[PowChallenge]]("failure-logger", consume=log_failure)
        weights_logger = SinkLoggerNode[WeightSettingSuccess]("weights-logger", consume=log_weights_set)
        error_logger = SinkLoggerNode[NexusException]("error-logger", consume=log_pipeline_error)
        # The task always emits its success-only converted output; we score from successful_task_result
        # instead, so drain it to keep the event bus from warning about an unconnected source.
        output_discard = SinkLoggerNode[PowSolution]("executor-output-discard", consume=discard)

        self.connect(self.subnet_clock.source, task.input)
        self.connect(task.successful_task_result, solution_logger.sink)
        self.connect(task.executor_failure, failure_logger.sink)
        self.connect(task.executor_output, output_discard.sink)
        self.connect(task.error, error_logger.sink)

        self.connect(epoch_clock.source, weight_setter.sink)
        self.connect(weight_setter.ok, weights_logger.sink)
        self.connect(weight_setter.error, error_logger.sink)


def _setup_sentry() -> None:
    # Optional Sentry integration. Enable by setting the SENTRY_DSN env var.
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    # Only load the Sentry libs if we actually need them
    import sentry_sdk
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.litestar import LitestarIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            LitestarIntegration(),
            LoggingIntegration(event_level=logging.ERROR),
            HttpxIntegration(),
            ThreadingIntegration(propagate_scope=True),
        ],
    )


@click.command()
@click.option("--env-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(env_file: Path | None) -> None:
    """CLI entry point: load env from --env-file (if given) and run the validator."""
    load_dotenv(env_file)
    logging_settings = LoggingSettings()
    configure_logging(logging_settings)
    setup_otel(OtelSettings())
    _setup_sentry()
    Validator.run(settings_class=Settings)


if __name__ == "__main__":
    main()
