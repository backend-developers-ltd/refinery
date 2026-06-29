# Refinery

A deliberately minimal, **local-only** Bittensor subnet whose only job is to generate genuine,
verifiable validator↔miner traffic on a local subtensor — so we can exercise and test the tooling
we build around subnets (Pylon, the Nexus validator runtime, observability, weight setting,
deployment scripts, logging). It is **not** meant for testnet or mainnet and has **no** economic value.

> Origin: rendered from the [Nexus subnet template](https://github.com/bittensor-church/nexus-subnet-template).
> The validator runs on the Nexus framework.

## What the subnet does

A trivial, trustless proof-of-work game:

1. Each block, the validator sends a miner a **challenge**: a random `seed` and a `difficulty` — find a
   `nonce` such that `SHA-256(seed || nonce)` has at least `difficulty` leading zero bits.
2. The miner brute-forces the `nonce` and returns it.
3. The validator verifies any answer with a **single hash** (ground truth is mathematical, never secret).
4. Each epoch, the validator weighs miners by **correctness** (the gate) and **speed** (a bounded bonus),
   so faster, reliable miners earn more — and slow miners that miss the deadline collapse to zero weight.

The single load knob is `difficulty`: low (8–12) ≈ near-instant "ping" traffic; high (24+) ≈ sustained,
observable CPU load. The full mechanism design lives in [`subnet_design.md`](subnet_design.md).

## Repository layout

- `validator/` — the Nexus-based validator (the production code; PoW challenge → miner → verify → weigh).
- `miner/` — a simple brute-force **test-fixture** miner with a configurable response delay
  (`MINER_RESPONSE_DELAY_S`) for simulating slow miners. Local-only; never a production miner.
- `localnet/` — local subtensor + Pylon + bootstrap + miner fixtures for end-to-end runs.
- `installer/`, `envs/deployed/`, `.github/workflows/` — operator install, deployed compose, and CI
  (inherited from the template; kept for completeness).

## Running it locally

See [`localnet/README.md`](localnet/README.md) for the end-to-end dev environment (subtensor + Pylon +
validator + miners). Validator configuration and the `VALIDATOR_*` knobs are documented in
[`validator/README.md`](validator/README.md).

## Development

Two independent `uv` projects — run `uv sync` inside `validator/` or `miner/` before working on either.
QA gates per project: `uv run ruff check`, `uv run basedpyright`, `uv run pytest`.
