# Refinery Validator

Validator node for the Refinery Bittensor subnet.

## What this is

A `docker compose` stack with two containers:

- **pylon** — sidecar that proxies all Bittensor / subtensor communication for the
  validator (handles wallet, weight setting, metagraph reads).
- **validator** — the Refinery validator process built from this repo.

## Running a validator

See [`installer/README.md`](../installer/README.md) for installation, configuration,
updates and prerequisites.

## Configuration knobs

All settings are `VALIDATOR_*` environment variables (defaults in parentheses):

| Variable                          | Effect                                                                 |
|-----------------------------------|------------------------------------------------------------------------|
| `VALIDATOR_NETUID`                | Subnet netuid the validator runs on (required).                        |
| `VALIDATOR_DIFFICULTY` (16)       | **Primary load knob** — required leading zero bits; +1 ≈ 2× the work.   |
| `VALIDATOR_CHALLENGE_DEADLINE` (10s) | Per-challenge end-to-end deadline; later answers count as failures. |
| `VALIDATOR_SEND_TIMEOUT` (2s)     | Timeout for the initial request to a miner.                            |
| `VALIDATOR_MAX_IN_FLIGHT` (16)    | Max concurrent in-flight challenges.                                   |
| `VALIDATOR_SPEED_WEIGHT` (0.25)   | How much faster miners are rewarded over the correctness baseline.     |
| `VALIDATOR_TARGET_LATENCY` (2s)   | Latency at/under which a miner gets the full speed bonus.              |
| `VALIDATOR_WEIGHT_SET_DELAY_BLOCKS` (0) | Blocks to wait after an epoch boundary before setting weights.   |

Logging is configured via `VALIDATOR_LOGGING_*` (see `src/validator/logging_config.py`).

## More

- Repository root `README.md` — what Refinery is and how the subnet works.
