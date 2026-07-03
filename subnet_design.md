# Refinery — Subnet Design

## Purpose and scope

Refinery is a **deliberately minimal, local-only Bittensor subnet**. It is **not** meant for testnet
or mainnet and provides **no real economic value**. Its sole job is to generate genuine, continuous,
*verifiable* validator↔miner traffic on a local subtensor so that we can exercise and test the tooling
we build around subnets (Pylon, the Nexus validator runtime, observability/metrics, dashboards, weight
setting, deployment scripts, logging).

Design goals, in priority order:

1. **Maximally simple** — trivial, deterministic task; no ML, no external data, no large dependencies.
2. **Real traffic & real load** — actual request/response cycles, scoring, and weight setting, with a
   single knob to dial CPU load up or down.
3. **Trustless, O(1) verification** — the validator confirms each answer with one hash; ground truth is
   mathematical, never validator-secret.

## What are we measuring?

> **How fast a miner produces a valid proof-of-work: a nonce whose hash meets the validator's
> difficulty target.**

(Single measurement: time-to-valid-PoW. Correctness is the *gate*; speed is the *measure*.)

## Mechanism mapping (Bittensor)

| Concern               | Choice                                                                       |
|-----------------------|------------------------------------------------------------------------------|
| Commodity             | Compute (fungible) — closest to the `compute_auction` pattern.               |
| Quality dimension     | Speed (latency to a valid solution), gated by correctness.                   |
| Verification          | Direct, immediate, trustless: recompute one hash.                            |
| Ground truth          | Mathematical (hash output) — **not** validator-controlled, no secret set.    |
| Compute distribution  | On miners (brute-force search); validator stays cheap (one hash to verify).  |
| Sybil note            | Out of scope — local-only, fixed miner set; UID pressure irrelevant here.    |

Invariant compliance: ✅ no secret eval sets · ✅ ground truth not validator-controlled ·
✅ no similarity detection · ✅ compute on miners · ✅ HTTP communication (no dendrite/synapse).

## The miner ↔ validator contract

The miner exposes an HTTP endpoint (served via the Nexus HTTP communicator). The validator discovers it
from the miner's on-chain axon info, exactly like any Nexus subnet.

### Challenge (validator → miner)

The validator generates, per miner per round, a fresh random challenge so nothing can be precomputed:

```json
{
  "challenge_id": "uuid",
  "seed": "<32 random bytes, hex>",
  "difficulty": 22,
  "deadline_s": 5.0
}
```

- `seed` — fresh CSPRNG bytes per challenge (un-precomputable).
- `difficulty` (`D`) — required number of **leading zero bits** of the digest. **This is the load knob.**
- `deadline_s` — wall-clock budget; answers arriving later are treated as failures.

### Solution (miner → validator)

```json
{
  "challenge_id": "uuid",
  "nonce": "<bytes, hex>"
}
```

The miner brute-forces `nonce` until `SHA-256(seed || nonce)` has at least `D` leading zero bits, then
returns it. (Any extra fields like an iteration count are informational only and are **not** trusted.)

### Verification (validator)

```
digest = SHA-256(seed || nonce)
valid  = leading_zero_bits(digest) >= D   AND   response received within deadline_s
```

One hash, O(1), trustless. A miner cannot fake a solution (the hash proves the work) and cannot
precompute (the seed is random per challenge).

## Round cadence and timing

The validator runs a continuous loop with **two decoupled frequencies**:

- **Per-block challenges** — on **every new block** the validator's subnet clock fires, and the validator
  sends **one** fresh challenge to **one** miner. This is what produces the steady request/response
  traffic; the block rate sets the baseline request rate.
  - **Miner selection follows the Nexus grain:** the framework routes one neuron per challenge (Nexus
    ships no broadcast/fan-out router). Refinery uses Nexus's round-robin neuron router, which picks a
    miner pseudo-randomly per challenge. Over a weight-setting window every miner is therefore challenged
    **many times, with roughly equal counts** (coverage equalizes statistically), rather than in a single
    synchronized broadcast. This keeps the validator maximally simple while still exercising every miner.
  - **Only servable miners are routable:** the router's neuron filter keeps a neuron only if it
    actually serves a reachable HTTP axon (`is_serving`, `protocol == HTTP`, valid port). A validator
    permit is **not** a disqualifier — a productive miner can accumulate enough stake to earn one while
    still serving as a miner, and must keep being challenged. Registered-but-unserved neurons — the
    validator's own hotkey, the subnet owner, offline miners — carry a zeroed axon (`ip=0.0.0.0`,
    `port=0`, `protocol=TCP`); challenging one would only make the HTTP communicator reject the target
    and waste the block's attempt.
  - Each challenge gets its **own fresh random `seed`**, so nothing can be shared, replayed, or
    precomputed across miners or rounds.
  - **Sampling stats accumulate over the window:** `success_rate` and average latency for each miner are
    computed across **all** of that miner's results in the epoch, so even one-challenge-per-block yields
    plenty of samples per miner before weights are set.

> **Note (deviation from an earlier sketch):** an earlier draft described a synchronized "full sweep of
> N challenges to every miner per round". Nexus routes one miner per challenge with no built-in broadcast,
> so the implemented design is the idiomatic per-block, round-robin form above — equivalent in outcome
> (equal coverage + per-epoch aggregation) and simpler. A strict round-robin or N-per-block fan-out could
> be added later with a small custom router/producer if exact per-round coverage is ever needed.
- **Weight setting** — separately, once per chain **epoch** (`tempo`, a locked hyperparameter). Between
  weight submissions the validator accumulates per-miner results across the rounds that occurred in that
  window, then normalizes and pushes weights via Pylon.

The two are intentionally independent: query traffic can be frequent for tooling load, while weights
still advance in the chain's natural rhythm. The exact wiring onto the Nexus engine/scheduler (which
component fires rounds, how the accumulation window aligns to `tempo`) is settled during validator
implementation; here we fix only the *intended behavior* and that both cadences are configurable.

## Scoring

The weighing function (run each epoch) reads the window's stored results and, per miner, counts:
`valid` solves (the recomputed hash meets `D`), `invalid` solves (a returned but wrong nonce), and
`failures` (timeouts / errors — answers that never arrived in time, recorded as executor failures). For
valid solves it also records the round-trip latency (`processing_finished − processing_started`).

- **Correctness gate** — `success_rate = valid / (valid + invalid + failures)`. This is the dominant
  term; with `D` tuned so a healthy miner reliably solves in time, honest miners cluster near `1.0`.
- **Speed bonus** — faster miners (lower average valid-solve latency) get a bounded bonus, creating a
  deliberate score spread so weight-setting tooling has something non-flat to act on.

```
speed_factor = clamp(target_latency / max(avg_solve_latency, eps), 0, 1)
weight       = success_rate * (1 + SPEED_WEIGHT * speed_factor)
```

with a small `SPEED_WEIGHT` (e.g. `0.25`). Pure correctness would also be a valid, even simpler mode
(`SPEED_WEIGHT = 0`) — kept configurable. A miner with zero valid solves gets weight `0`.

No validator-side EMA over scores/weights (per Bittensor guidance — `ema_smoothing: DISCOURAGED`). If
results are noisy, lengthen the window (more samples), not smoothing.

## Weight setting

Per-miner weights are emitted as **arbitrary non-negative floats** (Nexus's weight setter requires no
normalization — the chain/subnet applies its own). Miners with no valid solves in the window get `0`.
Verification happens here, at weighing time: only the stored result has both the seed the validator
*sent* and the nonce the miner *returned*, so a miner cannot pass by echoing a self-chosen easy seed.

## Knobs and how to turn them

Everything is driven by **environment variables**, consumed by a typed settings object on each side and
surfaced in the localnet `.env` / compose files. To turn a knob you edit the env value and restart the
relevant service (validator or a specific miner) — no code changes. Validator knobs use the project's
`VALIDATOR_*` convention; the miner fixture uses its own `MINER_*` variables, so the two sides are tuned
independently and per-service (you can run several miners with different settings at once).

### Validator knobs (`VALIDATOR_*`)

| Env var                             | Effect                                                              |
|-------------------------------------|--------------------------------------------------------------------|
| `VALIDATOR_DIFFICULTY`              | **Primary load knob.** +1 bit ≈ 2× expected hashes → exponential CPU.|
| `VALIDATOR_CHALLENGE_DEADLINE`      | End-to-end deadline per challenge; interacts with D to define pass/fail. |
| `VALIDATOR_SEND_TIMEOUT`            | Timeout for the initial request to a miner.                        |
| `VALIDATOR_MAX_IN_FLIGHT`           | Max concurrent in-flight challenges → request throughput.          |
| `VALIDATOR_SPEED_WEIGHT`            | How much score spread (weight movement) speed introduces.          |
| `VALIDATOR_TARGET_LATENCY`          | Latency at/under which a miner earns the full speed bonus.          |
| `VALIDATOR_WEIGHT_SET_DELAY_BLOCKS` | Blocks to wait after an epoch boundary before setting weights.      |

Set `D` low (e.g. 8–12) for near-instant "echo-like" traffic; raise it (e.g. 24+) to put the host's
CPUs under sustained, observable load.

### Miner-fixture knobs (`MINER_*`)

| Env var                  | Effect                                                                  |
|--------------------------|------------------------------------------------------------------------|
| `MINER_RESPONSE_DELAY_S` | Artificial delay before returning a solution. Simulates a slow miner.   |
| `MINER_NAME`             | Wallet/instance name (so several differently-tuned miners coexist).     |
| `MINER_NETUID`           | Subnet to register on (localnet default `2`).                           |

Running, say, three miners with `MINER_RESPONSE_DELAY_S = 0 / 1 / 4` on localnet lets us watch the speed
bonus spread the weights, and pushing one miner's delay past `VALIDATOR_CHALLENGE_DEADLINE` lets us watch
it start failing and its weight collapse — a direct end-to-end test of scoring and weight setting.

## Anti-gaming (brief — minimal because local-only)

- **Precompute** — prevented by a fresh random `seed` per challenge.
- **Forged solutions** — impossible; the validator recomputes the hash.
- **Relaying / outsourcing** — irrelevant and even acceptable under a compute-auction framing; not
  defended against, since there is no real economic stake.

## Components to build (high level)

- **Validator** (production code, in `validator/`): challenge generator, HTTP query of miners,
  verifier, scorer, weight normalization, main loop — implemented on the Nexus runtime. Ships with
  metrics (per Nexus conventions) and structured logging.
- **Miner** (test fixture only): a tiny brute-force solver exposing the HTTP endpoint, plus an optional
  configurable `response_delay_s` so we can simulate slow miners and verify that scoring and weights
  react correctly. It is meant to be **deployed on localnet so the whole thing looks and behaves like a
  real subnet** (registered on chain, axon discoverable, answering real queries). Because Refinery is
  local-only and exists to test tooling, the miner is legitimately test infrastructure
  (`localnet_miner_fixtures` exception) — never shipped or operated as a real production miner.

## Explicitly out of scope

Real incentive design, sybil resistance, economic value, mainnet/testnet deployment, commit-reveal,
adaptive difficulty (fixed configurable `D` is enough). These can be revisited only if the testing
needs change.
