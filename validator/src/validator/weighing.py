"""Epoch weighing: turn a window of stored PoW results into per-miner weights.

Verification happens here, not earlier in the pipeline: only at weighing time do we
have both the seed the validator *sent* (``executor_payload``) and the nonce the
miner *returned* (``executor_output``) for the same stored result. Verifying against
the echoed seed would let a miner cheat with a self-chosen easy seed, so the recompute
uses the stored challenge.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

from nexus.v1 import (
    BlockNumber,
    Epoch,
    Hotkey,
    NexusTaskName,
    Weight,
    WeightsCalculationBundle,
    get_logger,
)

from validator.proof_of_work import PowChallenge, PowSolution, solution_is_valid

logger: logging.Logger = get_logger(__name__)


def _previous_epoch(epoch: Epoch) -> Epoch:
    """Return the epoch immediately before the given one (same length)."""
    length = epoch.last_block - epoch.first_block + 1
    return Epoch(first_block=BlockNumber(epoch.first_block - length), last_block=BlockNumber(epoch.first_block - 1))


class _MinerTally:
    """Mutable per-miner accumulator used while aggregating one epoch's results."""

    def __init__(self) -> None:
        self.valid = 0
        self.invalid = 0
        self.failures = 0
        self.latencies: list[float] = []


class PowWeighing:
    """Weighing function: ``success_rate * (1 + speed_weight * speed_factor)`` per miner.

    Reads the epoch's successful results and executor-failure counts from the store,
    recomputes each solution's validity, and rewards correctness (the gate) with a
    bounded speed bonus so faster miners earn proportionally more.
    """

    def __init__(self, *, task_name: NexusTaskName, speed_weight: float, target_latency_s: float) -> None:
        self.task_name = task_name
        self.speed_weight = speed_weight
        self.target_latency_s = target_latency_s

    def __call__(self, bundle: WeightsCalculationBundle) -> Mapping[Hotkey, Weight]:
        store = bundle.tasks_result_store
        # The epoch beat carries the freshly-started epoch; weigh the just-completed one, whose
        # results are all in.
        epoch = _previous_epoch(bundle.epoch)
        successes = store.get_successful_tasks_for_epoch(self.task_name, epoch)
        failures = store.count_executor_failures_by_hotkey_for_epoch(self.task_name, epoch)

        tallies: dict[Hotkey, _MinerTally] = {}
        for result in successes:
            hotkey = Hotkey(result.target.hotkey)
            tally = tallies.setdefault(hotkey, _MinerTally())
            challenge = cast(PowChallenge, result.executor_payload)
            solution = cast(PowSolution, result.executor_output)
            if solution_is_valid(challenge.seed, solution.nonce, challenge.difficulty):
                tally.valid += 1
                tally.latencies.append((result.processing_finished - result.processing_started).total_seconds())
            else:
                tally.invalid += 1

        for hotkey, failure_count in failures.items():
            tallies.setdefault(hotkey, _MinerTally()).failures = failure_count

        weights: dict[Hotkey, Weight] = {hotkey: self._weight_for(tally) for hotkey, tally in tallies.items()}
        logger.info(
            "Weights computed for epoch %s-%s: %s",
            epoch.first_block,
            epoch.last_block,
            {hotkey: round(weight, 4) for hotkey, weight in weights.items()},
        )
        return weights

    def _weight_for(self, tally: _MinerTally) -> Weight:
        attempts = tally.valid + tally.invalid + tally.failures
        if attempts == 0 or tally.valid == 0:
            return Weight(0.0)
        success_rate = tally.valid / attempts
        avg_latency = sum(tally.latencies) / len(tally.latencies)
        speed_factor = min(1.0, max(0.0, self.target_latency_s / max(avg_latency, 1e-9)))
        return Weight(success_rate * (1.0 + self.speed_weight * speed_factor))
