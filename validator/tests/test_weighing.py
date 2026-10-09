from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

import pytest
from nexus.v1 import (
    BlockNumber,
    Epoch,
    Hotkey,
    NetUid,
    NexusTaskName,
    TaskResultStore,
    WeightsCalculationBundle,
)

from validator.proof_of_work import PowChallenge, PowSolution, leading_zero_bits, new_challenge, pow_digest
from validator.weighing import PowWeighing

TASK_NAME = NexusTaskName("pow_challenge")
NETUID = NetUid(3)
OTHER_NETUID = NetUid(5)
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


@dataclass(frozen=True)
class _Failure:
    target: _Target
    executor_payload: PowChallenge


class _FakeStore:
    def __init__(self, successes: list[_Result], failures: list[_Failure]) -> None:
        self._successes = successes
        self._failures = failures

    def get_successful_tasks_for_epoch(self, _task_name: NexusTaskName, _epoch: Epoch) -> tuple[_Result, ...]:
        return tuple(self._successes)

    def get_executor_failures_for_epoch(self, _task_name: NexusTaskName, _epoch: Epoch) -> tuple[_Failure, ...]:
        return tuple(self._failures)


def _solved_result(hotkey: str, difficulty: int, latency_s: float, netuid: NetUid = NETUID) -> _Result:
    challenge = new_challenge(netuid=netuid, block_number=1, difficulty=difficulty)
    return _Result(
        target=_Target(hotkey=hotkey),
        executor_payload=challenge,
        executor_output=PowSolution(challenge_id=challenge.challenge_id, nonce=_good_nonce(challenge.seed, difficulty)),
        processing_started=BASE_TIME,
        processing_finished=BASE_TIME + timedelta(seconds=latency_s),
    )


def _wrong_result(hotkey: str, difficulty: int, latency_s: float) -> _Result:
    challenge = new_challenge(netuid=NETUID, block_number=1, difficulty=difficulty)
    return _Result(
        target=_Target(hotkey=hotkey),
        executor_payload=challenge,
        executor_output=PowSolution(challenge_id=challenge.challenge_id, nonce=_bad_nonce(challenge.seed, difficulty)),
        processing_started=BASE_TIME,
        processing_finished=BASE_TIME + timedelta(seconds=latency_s),
    )


def _failure(hotkey: str, netuid: NetUid = NETUID) -> _Failure:
    return _Failure(
        target=_Target(hotkey=hotkey), executor_payload=new_challenge(netuid=netuid, block_number=1, difficulty=8)
    )


def _bundle(store: _FakeStore) -> WeightsCalculationBundle:
    return WeightsCalculationBundle(
        epoch=Epoch(first_block=BlockNumber(0), last_block=BlockNumber(1000)),
        tasks_result_store=cast(TaskResultStore[object, object, object], store),
    )


@pytest.fixture
def weighing() -> PowWeighing:
    return PowWeighing(task_name=TASK_NAME, netuid=NETUID, speed_weight=0.25, target_latency_s=1.0)


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
    return _bundle(_FakeStore(successes=successes, failures=[_failure("timeout"), _failure("timeout")]))


@pytest.fixture
def mixed_subnets_bundle() -> WeightsCalculationBundle:
    successes = [
        _solved_result("both", difficulty=8, latency_s=0.5),
        _solved_result("both", difficulty=8, latency_s=0.5, netuid=OTHER_NETUID),
        _solved_result("elsewhere", difficulty=8, latency_s=0.5, netuid=OTHER_NETUID),
    ]
    failures = [_failure("both", netuid=OTHER_NETUID), _failure("elsewhere-timeout", netuid=OTHER_NETUID)]
    return _bundle(_FakeStore(successes=successes, failures=failures))


def test_weighing_rewards_correctness_and_speed(weighing: PowWeighing, bundle: WeightsCalculationBundle) -> None:
    weights = weighing(bundle)

    assert weights == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
        {
            Hotkey("fast"): 1.25,
            Hotkey("slow"): 1.05,
            Hotkey("wrong"): 0.625,
            Hotkey("timeout"): 0.0,
        }
    )


def test_weighing_counts_only_its_own_subnet(
    weighing: PowWeighing, mixed_subnets_bundle: WeightsCalculationBundle
) -> None:
    assert weighing(mixed_subnets_bundle) == {Hotkey("both"): 1.25}


def test_weighing_is_empty_without_results(weighing: PowWeighing) -> None:
    assert weighing(_bundle(_FakeStore(successes=[], failures=[]))) == {}
