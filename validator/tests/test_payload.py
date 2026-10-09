from __future__ import annotations

from nexus.v1 import NetUid

from validator.payload import PowChallengePayloadCreator


def test_consecutive_blocks_challenge_subnets_in_turn() -> None:
    creator = PowChallengePayloadCreator(
        "pow-challenge-creator", netuids=[NetUid(3), NetUid(5), NetUid(6)], difficulty=8
    )

    assert [creator.netuid_for_block(block) for block in range(30, 36)] == [3, 5, 6, 3, 5, 6]
