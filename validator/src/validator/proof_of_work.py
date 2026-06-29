"""Core proof-of-work domain: challenge/solution models and trustless verification.

A challenge asks a miner to find a ``nonce`` such that
``SHA-256(seed_bytes || nonce)`` has at least ``difficulty`` leading zero bits.
The validator generates a fresh random ``seed`` per challenge (so nothing can be
precomputed) and verifies any answer with a single hash. The same hashing
convention must be mirrored by the miner — see ``miner/miner.py``.
"""

from __future__ import annotations

import hashlib
import os
import uuid

from pydantic import BaseModel

SEED_BYTES = 32
"""Number of random bytes in a challenge seed."""


class PowChallenge(BaseModel):
    """A proof-of-work challenge sent by the validator to a miner."""

    challenge_id: str
    seed: str
    difficulty: int
    block_number: int


class PowSolution(BaseModel):
    """A miner's answer: the nonce it claims satisfies the challenge."""

    challenge_id: str
    nonce: str


def leading_zero_bits(digest: bytes) -> int:
    """Count the leading zero bits of a byte string, most-significant byte first."""
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def pow_digest(seed: str, nonce: str) -> bytes:
    """Compute ``SHA-256(seed_bytes || nonce_utf8)`` for a hex ``seed`` and string ``nonce``."""
    return hashlib.sha256(bytes.fromhex(seed) + nonce.encode("utf-8")).digest()


def solution_is_valid(seed: str, nonce: str, difficulty: int) -> bool:
    """Return whether ``nonce`` solves the challenge for ``seed`` at the given ``difficulty``."""
    return leading_zero_bits(pow_digest(seed, nonce)) >= difficulty


def new_challenge(block_number: int, difficulty: int) -> PowChallenge:
    """Create a fresh challenge with a random seed for the given block and difficulty."""
    return PowChallenge(
        challenge_id=uuid.uuid4().hex,
        seed=os.urandom(SEED_BYTES).hex(),
        difficulty=difficulty,
        block_number=block_number,
    )
