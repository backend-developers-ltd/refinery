"""The BlockBeat-to-PowChallenge payload creator for the validator pipeline."""

from __future__ import annotations

from typing import override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    BlockBeat,
    Context,
    ContextStore,
    PayloadCreator,
    PipeToBus,
    TransformActor,
)

from validator.proof_of_work import PowChallenge, new_challenge


class PowChallengePayloadCreator(PayloadCreator[BlockBeat, PowChallenge], ActorBuilder):
    """Maps each BlockBeat into a fresh PowChallenge at the configured difficulty.

    sink input: BlockBeat from the validator's subnet clock
    source created_payload: PowChallenge addressed to a miner
    source error: payload creation failures
    """

    difficulty: int

    def __init__(self, _id: str, *, difficulty: int) -> None:
        super().__init__(_id)
        self.difficulty = difficulty

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
        return new_challenge(block_number=int(payload.block_number), difficulty=self.creator_spec.difficulty)
