from __future__ import annotations

import pytest

from validator.proof_of_work import (
    SEED_BYTES,
    leading_zero_bits,
    new_challenge,
    pow_digest,
    solution_is_valid,
)


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        (b"\xff", 0),
        (b"\x0f", 4),
        (b"\x00\x80", 8),
        (b"\x00\x00\xff", 16),
        (b"\x00\x00\x00", 24),
    ],
)
def test_leading_zero_bits(digest: bytes, expected: int) -> None:
    assert leading_zero_bits(digest) == expected


def _solve(seed: str, difficulty: int) -> str:
    counter = 0
    while True:
        nonce = str(counter)
        if leading_zero_bits(pow_digest(seed, nonce)) >= difficulty:
            return nonce
        counter += 1


def test_solution_is_valid_for_solved_nonce() -> None:
    challenge = new_challenge(block_number=10, difficulty=8)
    nonce = _solve(challenge.seed, challenge.difficulty)
    assert solution_is_valid(challenge.seed, nonce, challenge.difficulty) is True


def test_solution_is_invalid_for_tampered_nonce() -> None:
    challenge = new_challenge(block_number=10, difficulty=8)
    nonce = _solve(challenge.seed, challenge.difficulty)
    assert solution_is_valid(challenge.seed, nonce + "0", challenge.difficulty) is False


def test_solution_is_invalid_against_a_different_seed() -> None:
    challenge = new_challenge(block_number=10, difficulty=8)
    other = new_challenge(block_number=11, difficulty=8)
    nonce = _solve(challenge.seed, challenge.difficulty)
    assert solution_is_valid(other.seed, nonce, challenge.difficulty) is False


def test_new_challenge_carries_inputs_and_has_random_seed() -> None:
    challenge = new_challenge(block_number=42, difficulty=20)
    assert (challenge.block_number, challenge.difficulty) == (42, 20)
    assert len(bytes.fromhex(challenge.seed)) == SEED_BYTES
    assert challenge.seed != new_challenge(block_number=42, difficulty=20).seed
