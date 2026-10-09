"""The BlockBeat-to-PowChallenge payload creator for the validator pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from typing import override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    BlockBeat,
    Context,
    ContextStore,
    NetUid,
    PayloadCreator,
    PipeToBus,
    TransformActor,
)

from validator.proof_of_work import PowChallenge, new_challenge


class PowChallengePayloadCreator(PayloadCreator[BlockBeat, PowChallenge], ActorBuilder):
    """Maps each BlockBeat into a fresh PowChallenge at the configured difficulty.

    Consecutive blocks challenge the subnets in turn: block ``b`` targets ``netuids[b % len(netuids)]``,
    so the subnet is a pure function of the block and survives restarts without any stored state.

    sink input: BlockBeat from the validator's subnet clock
    source created_payload: PowChallenge addressed to a miner of the block's subnet
    source error: payload creation failures
    """

    netuids: tuple[NetUid, ...]
    difficulty: int

    def __init__(self, _id: str, *, netuids: Sequence[NetUid], difficulty: int) -> None:
        super().__init__(_id)
        self.netuids = tuple(netuids)
        self.difficulty = difficulty

    def netuid_for_block(self, block_number: int) -> NetUid:
        """Return the subnet whose miners the given block challenges."""
        return self.netuids[block_number % len(self.netuids)]

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return PowChallengePayloadCreatorActor(spec=self, pipe_to_bus=pipe_to_bus, context_store=context_store)


class PowChallengePayloadCreatorActor(TransformActor[BlockBeat, PowChallenge]):
    """Actor that turns each BlockBeat into a fresh, un-precomputable PowChallenge."""

    creator_spec: PowChallengePayloadCreator

    def __init__(
        self, *, spec: PowChallengePayloadCreator, pipe_to_bus: PipeToBus, context_store: ContextStore
    ) -> None:
        super().__init__(spec=spec, pipe_to_bus=pipe_to_bus, context_store=context_store)
        self.creator_spec = spec

    @override
    def _transform(self, ctx: Context, payload: BlockBeat) -> PowChallenge:
        block_number = int(payload.block_number)
        return new_challenge(
            netuid=self.creator_spec.netuid_for_block(block_number),
            block_number=block_number,
            difficulty=self.creator_spec.difficulty,
        )
