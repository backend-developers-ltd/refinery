# Localnet

Run a complete local subnet for development and testing.

Refinery is a proof-of-work subnet: each block the validator challenges a miner to find a `nonce`
whose `SHA-256(seed || nonce)` has `VALIDATOR_DIFFICULTY` leading zero bits; each epoch it weighs
miners by correctness (gate) and speed (bonus). Run the miner fixtures below with different
`--delay` values to watch fast miners out-earn slow ones and time-out miners drop to zero weight.

## Prerequisites

- Docker and Docker Compose
- uv

## Setup (one-time)

```sh
cp localnet/.env.example localnet/.env
```

Both `docker compose` and `bootstrap.py` read this file; create it before any of the steps below.

## Quick start (tmux)

```sh
localnet/run-in-tmux.sh
```

Runs the full flow: copies `.env` if missing, brings up docker compose, syncs validator
and miner deps, runs bootstrap, and starts validator + miner side by side in a tmux session
named `localnet` (2 panes, horizontal split). Detach: `Ctrl-b d`. Re-attach: `tmux attach -t localnet`.

Stop everything (kill tmux session + `docker compose down`):

```sh
localnet/stop-tmux.sh
```

If a session named `localnet` already exists, `run-in-tmux.sh` fails fast — run `stop-tmux.sh` first.

The sections below describe the same flow done by hand.

## Start infrastructure

```sh
cd localnet && docker compose up -d
```

Starts a local subtensor blockchain (port 9944) and a pylon proxy (port 8000).

## Bootstrap chain state

```sh
uv run localnet/bootstrap.py
```

Creates owner and validator wallets, funds them from Alice (pre-funded devnet account), creates subnet (netuid 2), and registers + stakes the validator. Idempotent — safe to re-run.

## Running the validator

```sh
cd validator && uv sync && uv run validator --env-file ../localnet/.env
```

The `localnet/.env` is pre-configured to connect to the local pylon. It also sets
`VALIDATOR_LOGGING_FORMAT=console` for pretty, colored console logs.

## Running the miner

```sh
cd miner && uv sync && uv run miner -n 1
```

This is the production miner from the `miner/` module. For simulating multiple miner profiles (honest, adversarial, etc.) against the validator, see "Miner fixtures" below.

## Miner fixtures

Standalone scripts in `localnet/miners/` that self-register and serve. The shipped profile,
`miner-honest.py`, brute-forces the PoW challenge. Its `--delay SECONDS` knob makes the same honest
solver stand in for a *slow* miner, so one fixture covers honest and slow behaviour.

Run three at once with different delays to exercise scoring and weight setting end to end:

```sh
uv run localnet/miners/miner-honest.py --name honest  --delay 0    # fast      -> highest weight
uv run localnet/miners/miner-honest.py --name slow     --delay 3    # slow      -> lower weight
uv run localnet/miners/miner-honest.py --name laggard  --delay 15   # > deadline -> 0 weight
```

Each `--name` is a distinct wallet, so they register as separate neurons. `-n N` spawns N instances of
one profile. After starting or changing miners, **restart pylon** (`cd localnet && docker compose
restart pylon`) so it refreshes its cached metagraph, then start the validator.

### Observing weights

Watch the validator log for `pow.solution` / `pow.failure` lines (per challenge) and
`Weights computed for epoch ...` (per epoch). Verify the result on-chain, directly via subtensor
(bypassing nexus/pylon):

```sh
cd miner && uv run python -c "
import bittensor as bt
st = bt.Subtensor(network='ws://127.0.0.1:9944')
for k, v in st.query_map('SubtensorModule', 'Weights', [2]):
    print(int(k.value), [(int(a), int(b)) for a, b in v.value])
"
```

Fast miners get the highest weight, slower miners less, and miners that exceed
`VALIDATOR_CHALLENGE_DEADLINE` get zero.

### Creating a new profile

Copy the template and customize:

```sh
cp localnet/miners/miner.template.py localnet/miners/miner-yourname.py
# edit MINER_NAME, run_task(), anything else necessary for the subnet
```

## Resetting

Full reset — clears chain state. Restart any running miners and validators afterwards so they re-register against the fresh chain.

```sh
cd localnet && docker compose down && docker compose up -d
```

Restart chain only:

```sh
cd localnet && docker compose restart subtensor
```

## Troubleshooting

**Pylon: `Failed to parse keyfile data` / weights never reach the chain.** Pylon signs `set_weights`
with the validator hotkey, so it must be able to read the keyfile. `bittensor-wallet` 4.1.0+ writes a
keyfile format that pylon 2.0.0 (bittensor-wallet 4.0.1) cannot parse. `bootstrap.py` and the miner
fixtures therefore **pin `bittensor-wallet==4.0.1`** (matching pylon and `miner/uv.lock`). If you
unpin them, fresh wallets will break weight setting — keep these in sync.

**`docker compose up` fails with `open sysctl net.ipv4.ip_unprivileged_port_start: permission denied`.**
This happens inside restricted/nested Docker (e.g. some sandboxes) where containers can't set the
default sysctl. Work around it by running subtensor and pylon with host networking via a local,
uncommitted compose override (set `network_mode: host`, clear `ports`, and point pylon at
`PYLON_BITTENSOR_NETWORK=ws://127.0.0.1:9944`), then `docker compose -f compose.yml -f override.yml up`.
On a normal Docker host the default bridge networking in `compose.yml` works as-is.
