# Refinery Validator

Validator node for the Refinery Bittensor subnet.

## What this is

A `docker compose` stack whose core containers are:

- **pylon** — sidecar that proxies all Bittensor / subtensor communication for the
  validator (handles wallet, weight setting, metagraph reads).
- **validator** — the Refinery validator process built from this repo.
- **alloy** — Grafana Alloy sidecar that collects the validator's OpenTelemetry traces and forwards
  them to the configured OTLP upstream — the observability proxy or a Tempo backend.

## Observability

The validator ships structured JSON logs (`structlog`) and OpenTelemetry traces. The traces
upstream is configured via `TRACES_UPSTREAM_URL` / `TRACES_UPSTREAM_USER` / `TRACES_UPSTREAM_PASSWORD`.

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
