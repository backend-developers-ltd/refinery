from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

import pytest
from nexus.v1 import (
    BlockNumber,
    Epoch,
    Hotkey,
    NexusTaskName,
    TaskResultStore,
    WeightsCalculationBundle,
)

from validator.proof_of_work import PowChallenge, PowSolution, leading_zero_bits, new_challenge, pow_digest
from validator.weighing import PowWeighing

TASK_NAME = NexusTaskName("pow_challenge")
BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def _good_nonce(seed: str, difficulty: int) -> str:
    counter = 0
    while True:
        nonce = str(counter)
        if leading_zero_bits(pow_digest(seed, nonce)) >= difficulty:
            return nonce
        counter += 1


def _bad_nonce(seed: str, difficulty: int) -> str:
    counter = 0
    while True:
        nonce = str(counter)
        if leading_zero_bits(pow_digest(seed, nonce)) < difficulty:
            return nonce
        counter += 1


@dataclass(frozen=True)
class _Target:
    hotkey: str
    uid: int = 0


@dataclass(frozen=True)
class _Result:
    target: _Target
    executor_payload: PowChallenge
    executor_output: PowSolution
    processing_started: datetime
    processing_finished: datetime


class _FakeStore:
    def __init__(self, successes: list[_Result], failures: Mapping[str, int]) -> None:
        self._successes = successes
        self._failures = failures

    def get_successful_tasks_for_epoch(self, _task_name: NexusTaskName, _epoch: Epoch) -> tuple[_Result, ...]:
        return tuple(self._successes)

    def count_executor_failures_by_hotkey_for_epoch(
        self, _task_name: NexusTaskName, _epoch: Epoch
    ) -> Mapping[Hotkey, int]:
        return Counter({Hotkey(hotkey): count for hotkey, count in self._failures.items()})


def _solved_result(hotkey: str, difficulty: int, latency_s: float) -> _Result:
    challenge = new_challenge(block_number=1, difficulty=difficulty)
    return _Result(
        target=_Target(hotkey=hotkey),
        executor_payload=challenge,
        executor_output=PowSolution(challenge_id=challenge.challenge_id, nonce=_good_nonce(challenge.seed, difficulty)),
        processing_started=BASE_TIME,
        processing_finished=BASE_TIME + timedelta(seconds=latency_s),
    )


def _wrong_result(hotkey: str, difficulty: int, latency_s: float) -> _Result:
    challenge = new_challenge(block_number=1, difficulty=difficulty)
    return _Result(
        target=_Target(hotkey=hotkey),
        executor_payload=challenge,
        executor_output=PowSolution(challenge_id=challenge.challenge_id, nonce=_bad_nonce(challenge.seed, difficulty)),
        processing_started=BASE_TIME,
        processing_finished=BASE_TIME + timedelta(seconds=latency_s),
    )


@pytest.fixture
def bundle() -> WeightsCalculationBundle:
    successes = [
        _solved_result("fast", difficulty=8, latency_s=0.5),
        _solved_result("fast", difficulty=8, latency_s=0.5),
        _solved_result("slow", difficulty=8, latency_s=5.0),
        _solved_result("slow", difficulty=8, latency_s=5.0),
        _solved_result("wrong", difficulty=8, latency_s=0.5),
        _wrong_result("wrong", difficulty=8, latency_s=0.5),
    ]
    store = _FakeStore(successes=successes, failures={"timeout": 2})
    return WeightsCalculationBundle(
        epoch=Epoch(first_block=BlockNumber(0), last_block=BlockNumber(1000)),
        tasks_result_store=cast(TaskResultStore[object, object, object], store),
    )


def test_weighing_rewards_correctness_and_speed(bundle: WeightsCalculationBundle) -> None:
    weighing = PowWeighing(task_name=TASK_NAME, speed_weight=0.25, target_latency_s=1.0)

    weights = weighing(bundle)

    assert weights == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
        {
            Hotkey("fast"): 1.25,
            Hotkey("slow"): 1.05,
            Hotkey("wrong"): 0.625,
            Hotkey("timeout"): 0.0,
        }
    )


def test_weighing_is_empty_without_results() -> None:
    weighing = PowWeighing(task_name=TASK_NAME, speed_weight=0.25, target_latency_s=1.0)
    empty = WeightsCalculationBundle(
        epoch=Epoch(first_block=BlockNumber(0), last_block=BlockNumber(1000)),
        tasks_result_store=cast(TaskResultStore[object, object, object], _FakeStore(successes=[], failures={})),
    )

    assert weighing(empty) == {}
