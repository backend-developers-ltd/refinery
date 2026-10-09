# Refinery — Linode deployment

This directory deploys the full Refinery subnet across **three Linode machines** on a private VLAN:

| Role | Machine | Runs | Source of compose |
|------|---------|------|-------------------|
| **chain** | `refinery-chain` | `subtensor-localnet` (own local blockchain) + an archive full node | `deploy/linode/localchain/docker-compose.yml` |
| **validator** | `refinery-validator` | validator + pylon + full metrics stack | `envs/deployed/docker-compose.yml` (the standard prod stack) |
| **miner** | `refinery-miner` | the `refinery-miner` test-fixture miner | `deploy/linode/miner/docker-compose.yml` |
| **multi-validator** (optional) | `refinery-multi-validator` | validator + pylon on every subnet from netuid 3 up | `deploy/linode/multi-validator/docker-compose.yml` |

Linode is just a (more elaborate) **prod instance**: it runs the *same* published images
(`refinery-validator-prod`, `refinery-miner-prod`) as any other prod deploy — it merely also hosts its
own chain and a miner alongside the validator. The validator therefore reuses the top-level
`installer/` + `envs/deployed/` machinery unchanged; only its `.env` points at the local chain.

The optional fourth machine validates the other subnets created on this chain — see
[§10](#10-multi-subnet-validator--validate-every-subnet-from-netuid-3-up).

All machines keep themselves up to date with a 15-minute cron job that re-pulls their
docker-compose from the `deploy-config-prod` branch (see [Updates](#7-updates)).

```
                 Linode private VLAN (10.0.0.0/24)
   ┌────────────────────┐  ws://10.0.0.10:9944  ┌────────────────────┐
   │ refinery-chain     │◀──────────────────────│ refinery-validator │
   │ 10.0.0.10          │◀───────────┐          │ 10.0.0.20          │
   │ subtensor 9944/9933│            │ ws 9944  │ validator + pylon  │
   │ archive 9945       │            │          │ + metrics          │
   └────────────────────┘            │          └─────────┬──────────┘
                       ┌─────────────┴──────┐    callback │ ▲ POST /task
                       │ refinery-miner      │    :8001 ◀──┘ │  to axon
                       │ 10.0.0.30           │───────────────┘
                       │ refinery-miner axon │
                       └─────────────────────┘
```

The VLAN IPs above (`10.0.0.10/20/30`) are an example used throughout this guide — substitute your own.

---

## 1. Create and prepare the machines (from zero)

### 1.1 Create three Linodes

In the Linode Cloud Manager, create three Linodes, all **in the same region** (VLANs are regional):

- `refinery-chain` — the local blockchain. CPU is modest, but it runs **three** colocated authority
  nodes: give it at least 4 GB (2 GB OOMs, even with the cache caps described in §2).
- `refinery-validator` — validator + pylon + metrics. Give it the most headroom (e.g. 4 GB).
- `refinery-miner` — the brute-force miner. PoW is CPU-bound; a dedicated-CPU plan mines faster.

Use a recent Ubuntu LTS image and add your SSH key to each.

### 1.2 Attach all three to one private VLAN

For each Linode: **Network → Add a VLAN**, attach all three to the *same* VLAN label (e.g.
`refinery`), and assign static addresses:

| Machine | VLAN IP (example) |
|---------|-------------------|
| `refinery-chain` | `10.0.0.10/24` |
| `refinery-validator` | `10.0.0.20/24` |
| `refinery-miner` | `10.0.0.30/24` |

A Linode VLAN is an isolated layer-2 network — only your three Linodes can talk on it, and nothing on
it is reachable from the public internet. After (re)attaching a VLAN the Linode must be rebooted so the
extra interface (usually `eth1`) comes up. Confirm with `ip -4 addr show eth1`.

### 1.3 Lock down the public interface

Every service in this deployment is **bound to the VLAN IP** (the chain RPC, the validator callback,
the miner axon), so nothing listens on the public interface. Add a **Linode Cloud Firewall** to all
three machines that, on the public interface, allows only **inbound** SSH from your admin IP and drops
everything else inbound. (Cloud Firewall governs the public interface; the VLAN provides the
private-side isolation.) Optionally tighten further with a host firewall on the VLAN interface — see
[§8 Security](#8-security-notes).

**Leave outbound (egress) open.** All three machines pull Docker images and the auto-update compose
over the internet, and crucially the **chain machine's Drand offchain worker must reach `api.drand.sh`**
(quicknet) for timelocked commit-reveal to reveal weights. Restrict *inbound* only; do not block egress.

### 1.4 Create the deployment user

Linode logs you in as **`root`** by default — don't run the deployment as root. On **each** of the
three machines, create a dedicated sudo user (called `ubuntu` throughout this guide) and do
everything below as that user. The install scripts use `$HOME`/`$USER` and a per-user `crontab`, so
they work unchanged under any non-root user; only the one-time Docker/cron install (§1.5) needs
`sudo`.

As `root`, on each machine:

```bash
# Create the user and grant sudo
adduser --disabled-password --gecos "" ubuntu
usermod -aG sudo ubuntu

# Grant passwordless sudo. `--disabled-password` locks the account's password, so `sudo` (which
# by default prompts for the user's own password) could never authenticate. Passwordless sudo
# matches the standard cloud `ubuntu` user and suits SSH-key-only login + the non-interactive
# install scripts and cron jobs below.
echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ubuntu
chmod 440 /etc/sudoers.d/90-ubuntu
visudo -cf /etc/sudoers.d/90-ubuntu   # verify syntax before relying on it

# Copy your SSH key over so you can log in as `ubuntu`
rsync --archive --chown=ubuntu:ubuntu ~/.ssh /home/ubuntu/
```

Now **open a fresh SSH session as `ubuntu`** and confirm both login and `sudo` work. Only once that
succeeds, harden SSH by disabling root login:

```bash
# as ubuntu, on each machine
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

From here on, run **every** step in this guide as `ubuntu` (the `~/…` paths then resolve under
`/home/ubuntu/`).

### 1.5 Install Docker + cron on each machine

On **each** of the three machines, install **Docker Engine + the compose plugin** from Docker's apt
repository, following the official guide:
<https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository>. (Use the apt-repository
method, not the convenience script — the latter is explicitly not recommended for production.)

Then add your user to the `docker` group, install cron (used by the auto-update job) and cap the systemd
journal, which otherwise grows to 10% of the disk before it starts pruning itself:

```bash
sudo usermod -aG docker "$USER"   # log out/in afterwards so the group takes effect

# cron (used by the auto-update job)
sudo apt-get update && sudo apt-get install -y cron
sudo systemctl enable --now cron

# journald: the stock cap is 10% of the filesystem (8 GB on an 80 GB disk). Skip this if your host
# management already ships a cap (check /etc/systemd/journald.conf.d/ - drop-ins sorting later win).
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=1G\n' | sudo tee /etc/systemd/journald.conf.d/50-refinery.conf
sudo systemctl restart systemd-journald
```

Verify: `docker compose version`, `systemctl is-active cron` and `journalctl --disk-usage`.

---

## 2. Chain machine — bring up subtensor and bootstrap the subnet

On **`refinery-chain`**:

```bash
mkdir -p ~/refinery-chain && cd ~/refinery-chain
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/localchain/.env.example -o .env
# Edit .env: set CHAIN_BIND_IP to this machine's VLAN IP (e.g. 10.0.0.10)
nano .env

# Install + start the chain (also installs the 15-min auto-update cron)
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/install.sh \
  | bash -s -- localchain prod ~/refinery-chain
```

> **If the subtensor container crash-loops** with `Cannot allocate memory (os error 12)`, the kernel is
> refusing the (never-touched) virtual memory the 3-node chain's WASM runtimes reserve. Raise the limits
> and restart: `sudo sysctl -w vm.overcommit_memory=1 vm.max_map_count=262144`, persist them with
> `printf 'vm.overcommit_memory=1\nvm.max_map_count=262144\n' | sudo tee /etc/sysctl.d/99-subtensor.conf`,
> then `docker compose up -d`.

> **Memory caps.** Substrate defaults to a **1 GiB trie cache plus a 1 GiB RocksDB memory budget per
> node** (a third of the budget is block cache), so the three authorities grow towards ~4 GB of caches
> alone and OOM-kill the machine every few hours. `deploy/linode/localchain/docker-compose.yml` therefore
> wraps the image's entrypoint to patch `--trie-cache-size 67108864 --db-cache 256` into each authority's
> argv (the image's `localnet.sh` hardcodes them and takes no node flags of its own), and runs the
> container under a 3 GB `mem_limit` with swap disabled. **Never set `--db-cache` below 128.** Substrate
> hands 90% of it to the STATE column and splits the rest across the other 12 RocksDB columns in whole
> MiB, so 64 left those columns with a budget of 0 (64 KB write buffers, `max_bytes_for_level_base=0`)
> and every flush rewrote a whole column: write amplification ~70 000, 61 TB rewritten per authority in
> 24 days, the compaction thread at 90% of each node's CPU and RocksDB's info `LOG` growing 180 MB/day per
> node. 256 gives each side column 2 MiB for ~210 MB of RocksDB memory per node. If an image bump changes
> `localnet.sh` so the patch stops applying, the container refuses to start and logs `FATAL: expected to
> cap 3 authorities' caches`. Note that an OOM kill lands on a single `node-subtensor`, never on PID 1, so
> the container can stay "up" with 2/3 authorities — below GRANDPA's quorum — while the chain quietly
> stops finalizing; check `docker compose exec subtensor pgrep -c node-subtensor` (expect `3`) and
> `docker compose restart subtensor` to recover.

> **Archive node.** The three authorities keep only the last ~256 blocks of *state* (Substrate's
> default state pruning, fixed when the DB is created — it cannot be switched on an existing DB), so
> reading older state fails with `State already discarded`. Readers that backfill history (the TAO20
> attester's 8-day price windows) use the separate `archive` service instead: a non-validating full node
> with `--state-pruning archive` that syncs every block from genesis off authority One (bootnode peer id
> derived from One's persisted node key, chain spec rebuilt from the same binary) and serves
> `ws://10.0.0.10:9945`. It is a pure reader — if it dies, block production and finality are untouched,
> and on restart it resumes syncing; `docker compose logs archive` should show it importing at the
> authorities' height. A bounded window (`--state-pruning 72000`, RocksDb or ParityDb) is not viable on
> this box: the node keeps ~65 KB of RAM per block of the window and OOMs at its 1 GiB cap. The archive
> instead costs **disk**: it keeps every trie node ever written, ~60 KB of state per idle block, i.e.
> ~0.4 GB/day (up to ~150 KB/block while the Drand pallet catches up). A resync from genesis rebuilds
> exactly that history, so deleting the `chain_archive` volume only reclaims RocksDB slack (WAL,
> obsolete SSTs, the info `LOG`), never the state itself — more disk is the only real remedy. Keep an eye
> on the volume.

> **Disk hygiene.** Three things on this box grow without bound unless capped: container logs (the
> compose file caps every service at 5 × 50 MB via the json-file driver), the systemd journal (capped in
> §1.5) and RocksDB's info `LOG` inside each chain volume, which Substrate never rotates. The installer
> therefore adds a weekly cron (`REFINERY_LOCALCHAIN_ROCKSDB_LOG`, Sunday 04:00) that truncates any of
> those `LOG` files whose allocated size exceeds 256 MB; truncating in place is safe, RocksDB keeps
> appending and the file merely turns sparse. The WAL sawtooth is normal: RocksDB keeps up to ~1 GB of
> write-ahead log per node and purges it in one go roughly daily, so keep at least ~6 GB of headroom on `/`.

The chain is now serving on `ws://10.0.0.10:9944` (VLAN only; historical state on `ws://10.0.0.10:9945`),
running standard **12s blocks** with **persistent state** (survives restarts/reboots). Next, **bootstrap the subnet once**:
create the subnet, set its hyperparameters, and register + stake the validator. This needs `uv`:

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.profile

# Fetch and run the bootstrap script against the local chain (via its VLAN IP)
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/localnet/bootstrap.py -o bootstrap.py
NETUID=2 SUBNET_TEMPO=360 \
  SUBNET_COMMIT_REVEAL_ENABLED=true SUBNET_REVEAL_PERIOD_EPOCHS=1 \
  BOOTSTRAP_SUBTENSOR_NETWORK=ws://10.0.0.10:9944 \
  BOOTSTRAP_WALLET_DIR="$HOME/refinery-chain/wallets" \
  uv run bootstrap.py
```

This creates the subnet at **netuid 2**, the `owner` and `validator` wallets under
`~/refinery-chain/wallets/`, funds them from the devnet `//Alice` key, registers + stakes the
validator, normalizes the genesis subnets' (0/1) tempo to the standard 360, and enables **timelocked
commit-reveal** on the subnet. It is idempotent — safe to re-run.

- `SUBNET_COMMIT_REVEAL_ENABLED=true` turns on the chain's v4 timelocked commit-reveal (pylon then
  commits weights timelock-encrypted and they auto-reveal one epoch later). This is appropriate here
  because the chain runs standard **12s blocks**; the dev localnet leaves it off (fast blocks).
- Timelocked reveal depends on the chain node's **Drand offchain worker** reaching `api.drand.sh`
  (quicknet). Ensure this machine has **outbound internet** (§1.3) — without it, commits never reveal,
  so no weights are set and miners earn nothing.

> **Chain persistence.** The chain DB is **persistent**: `deploy/linode/localchain/docker-compose.yml`
> runs the non-fast-runtime binary with `--no-purge` and mounts a volume per authority node. Restarting
> the `subtensor` service, bumping its image, or rebooting this machine all **resume the existing
> chain** — they no longer wipe genesis. Run this bootstrap step **once**; the validator + miners stay
> registered across restarts. (Only `docker compose down -v`, which deletes the volumes, resets the
> subnet — see §9.)

---

## 3. Distribute the validator wallet

Pylon (on the validator machine) signs `set_weights` with the validator **hotkey**, so that key must
live on the validator machine. The **coldkey must NOT** be copied there (security). On
**`refinery-chain`**:

```bash
cd ~/refinery-chain/wallets
# Public coldkey + hotkey only — never validator/coldkey
tar czf /tmp/validator-wallet.tgz validator/coldkeypub.txt validator/hotkeys/
```

Copy `/tmp/validator-wallet.tgz` to **`refinery-validator`** (e.g. via `scp` through your admin host)
and extract it into the validator's wallet directory:

```bash
# on refinery-validator
mkdir -p ~/.bittensor/wallets && tar xzf validator-wallet.tgz -C ~/.bittensor/wallets
# Expected layout (NO coldkey file):
#   ~/.bittensor/wallets/validator/coldkeypub.txt
#   ~/.bittensor/wallets/validator/hotkeys/default
```

---

## 4. Validator machine — install the standard prod stack

The validator uses the **top-level installer** (same as any prod validator). On
**`refinery-validator`**, run it and answer the prompts:

```bash
curl -s https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/installer/install.sh | bash
```

Answer the prompts:

- `BITTENSOR_NETWORK` → `ws://10.0.0.10:9944` (the chain's VLAN IP)
- `HOST_WALLET_DIR` → `~/.bittensor/wallets` (where you extracted the wallet in §3)
- `BITTENSOR_WALLET_NAME` → `validator`
- `BITTENSOR_WALLET_HOTKEY_NAME` → `default`
- `SENTRY_DSN` → leave empty

Then apply the **three localnet-specific settings** to `~/refinery-validator/.env`:

```bash
cd ~/refinery-validator
sed -i 's/^NETUID=.*/NETUID=2/' .env                                  # subnet is netuid 2, not 1
cat >> .env <<'EOF'
PYLON_BLOCK_DURATION_SECONDS=12
VALIDATOR_CALLBACK_HOST=10.0.0.20
EOF
docker compose up -d            # apply the corrected config
```

- `NETUID=2` — the installer defaults to `1`; our bootstrapped subnet is `2`.
- `PYLON_BLOCK_DURATION_SECONDS=12` — match the chain's standard 12s blocks (the chain runs the
  non-fast-runtime binary, not 250 ms fast blocks).
- `VALIDATOR_CALLBACK_HOST` — this machine's VLAN IP. The validator advertises
  `http://10.0.0.20:8001` as the callback URL miners POST solutions to, and the compose binds the
  published `8001` to this IP. Open `8001` from the miner in your firewall.

Check health: `docker compose ps` and `docker compose logs -f validator pylon`.

---

## 5. Miner machine — install the miner

On **`refinery-miner`**:

```bash
mkdir -p ~/refinery-miner && cd ~/refinery-miner
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/miner/.env.example -o .env
# Edit .env: CHAIN_VLAN_IP=10.0.0.10, MINER_VLAN_IP=10.0.0.30, MINER_AXON_PORT=18000, NETUIDS=2
nano .env

curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/install.sh \
  | bash -s -- miner prod ~/refinery-miner
```

The miner creates its own wallet (in a `miner-wallets` volume), self-funds from `//Alice`, registers
on every netuid in `NETUIDS` (here just 2), and advertises its axon at `10.0.0.30:18000` on each of them. Open `18000` from the validator in your
firewall.

Run a slow miner instead by setting `MINER_RESPONSE_DELAY_S` (e.g. `3`) in `.env` — watch its weight
drop relative to a fast one. To run several distinct miners, deploy more miner machines (each with a
distinct `MINER_NAME` and `MINER_VLAN_IP`).

---

## 6. Verify end to end

- **Validator log** (`refinery-validator`): `docker compose logs -f validator` — expect `pow.solution`
  lines per challenge and `Weights computed for epoch ...` once per epoch (**~72 min** at tempo 360 ×
  12 s/block).
- **Miner log** (`refinery-miner`): `docker compose logs -f miner` — expect `Received request` /
  `Responded to` lines.
- **Commit-reveal timing.** With timelocked commit-reveal on, pylon *commits* weights each epoch and
  they **auto-reveal one epoch later** (`SUBNET_REVEAL_PERIOD_EPOCHS=1`). So on-chain `Weights[2]` first
  appear ~2 epochs (**~2.5 h**) after the validator starts, not immediately. Until the first reveal,
  `WeightCommits`/`CRV3WeightCommitsV2` populate but `Weights[2]` stays empty — that's expected.
- **On-chain weights** — from any machine on the VLAN (needs `uv`):

  ```bash
  uv run --with 'bittensor==10.3.0' python -c "
  import bittensor as bt
  st = bt.Subtensor(network='ws://10.0.0.10:9944')
  for k, v in st.query_map('SubtensorModule', 'Weights', [2]):
      print(int(k.value), [(int(a), int(b)) for a, b in v.value])
  "
  ```

If the validator logs challenges but never gets solutions, the axon/callback path across the VLAN is
blocked — re-check `MINER_AXON_EXTERNAL_IP`/`VALIDATOR_CALLBACK_HOST` and that ports `18000`
(validator→miner) and `8001` (miner→validator) are open on the VLAN.

---

## 7. Updates

### Operator side (automatic)

Every machine runs a 15-minute cron job that re-pulls its docker-compose from `deploy-config-prod` and
restarts the stack **only if the file changed** (i.e. when a new image digest is pinned):

- chain: cron tag `REFINERY_LOCALCHAIN_UPDATE`, compose `deploy/linode/localchain/docker-compose.yml`
  (plus the weekly `REFINERY_LOCALCHAIN_ROCKSDB_LOG` truncation job, see the disk-hygiene note in §2)
- miner: cron tag `REFINERY_MINER_UPDATE`, compose `deploy/linode/miner/docker-compose.yml`
- validator: cron tag from the top-level installer, compose `envs/deployed/docker-compose.yml`
- multi-validator: cron tag `REFINERY_MULTI-VALIDATOR_UPDATE`, compose
  `deploy/linode/multi-validator/docker-compose.yml`

Each machine updates independently — a validator or miner update never touches the chain machine. The
chain restarts only when its **own** compose changes, and because its state is persistent that restart
no longer wipes the subnet. One caveat: bumping the chain to an image with a **different genesis** would
make the persisted DB incompatible (the node would fail to start on the old volumes); a deliberate genesis
change therefore requires a one-time `docker compose down -v` + re-bootstrap, not just an image bump.

Force an update now: re-run the relevant `install.sh`/`update_compose.sh`, or
`cd <workdir> && docker compose pull && docker compose up -d`.

### Developer side — build & promote images

Images are released through the **standard prod pipeline** and pinned by `@sha256` digest (never by
mutable tag). Full rules and the validator procedures are in
[`knowledge/validator.deploy.md`](../knowledge/validator.deploy.md).

**Build (procedure 1).** Fast-forward `master` → `deploy-build-prod`. This fires both
`build-validator.yml` and `build-miner.yml`, producing `refinery-validator-prod` and
`refinery-miner-prod` tagged `:v0-latest` and `:sha-<commit>`:

```bash
git push origin master:deploy-build-prod
```

**Promote the validator image (procedure 2).** As in `knowledge/validator.deploy.md`: resolve the
digest, smoke-test, pin it into `envs/deployed/docker-compose.yml`, push `master` →
`deploy-config-prod`. The multi-validator (§10) runs the same image: pin its digest in the `validator`
service of `deploy/linode/multi-validator/docker-compose.yml` the same way — the two pins are
independent, so either machine can be promoted on its own.

**Promote the miner image.** Same shape, for `deploy/linode/miner/docker-compose.yml`:

```bash
# 1. Resolve the digest of the built image (tag is used once, then discarded)
docker buildx imagetools inspect \
  ghcr.io/backend-developers-ltd/refinery-miner-prod:sha-<commit> \
  --format '{{json .Manifest.Digest}}'

# 2. Pin it in deploy/linode/miner/docker-compose.yml (the miner service `image:`), replacing the
#    sha256:0000... placeholder:
#    image: ghcr.io/backend-developers-ltd/refinery-miner-${ENVIRONMENT:?}@sha256:<digest>

# 3. Commit and ship to operators
git commit -am "chore(deploy): pin prod miner to <digest-prefix>"
git push origin master:deploy-config-prod
```

The miner machines' cron picks up the changed compose within 15 minutes and restarts onto the pinned
image.

---

## 8. Security notes

- **No coldkey on the validator.** Only `coldkeypub.txt` + the hotkey are copied to the validator
  machine (§3). Keep the validator coldkey on the chain machine / offline.
- **Nothing on the public interface.** Every service binds to a VLAN IP. The public Cloud Firewall
  should allow only SSH from your admin IP.
- **Non-root deployment user.** The stack runs as an unprivileged `ubuntu` user (§1.4), not root,
  and root SSH login is disabled. Combined with the SSH-only Cloud Firewall this removes the direct
  root entry point. (Note: membership in the `docker` group is effectively root-equivalent, so the
  real gains are a disabled root login and tidy per-user state, not container isolation.)
- **Defense in depth on the VLAN (optional).** Add a host firewall (e.g. `ufw`) restricting the VLAN
  interface to exactly: `9944` (and `9945` for archive readers) inbound on the chain from the validator
  + miner IPs; `8001` inbound on
  the validator from the miner IP; `MINER_AXON_PORT` inbound on the miner from the validator IP.
- **Devnet keys.** This subnet funds everything from the well-known `//Alice` devnet key on its own
  local chain. It has no economic value and must never be pointed at testnet or mainnet.

---

## 9. Teardown

Per machine: `cd <workdir> && docker compose down` and remove the cron line
(`crontab -l | grep -v REFINERY_ | crontab -`). Named volumes survive a plain `down`: the chain keeps
its DB (subnet, registrations, stake) and the miner keeps its wallet, so `down` + `up -d` resumes where
you left off. To **fully reset** — wipe all chain state or the miner wallet — use `docker compose down
-v`, which deletes the volumes. Tearing down the chain machine's volumes discards all subnet state
(the archive node's history included).

---

## 10. Multi-subnet validator — validate every subnet from netuid 3 up

Other projects create their own subnets on this chain (netuid 3 and up). The `multi-validator` role runs
**one** Refinery validator on all of them at once, so each of those subnets gets a validator that sets
weights and the miner machine's miner earns incentive on each. It needs one more machine,
`refinery-multi-validator` (here `10.0.0.60`; 1 vCPU / 2 GB is enough), set up as in §1, and reuses
the miner machine: the miner registers on the same subnets and serves all of them from its one axon.

One process covers many subnets like this: each block challenges a miner of the next subnet in
`NETUIDS`, in turn (with 26 subnets each one gets ~14 challenges per epoch, so a subnet with more miners
than that leaves some unsampled each epoch), and every subnet has its own epoch clock and weight setter.
Pylon binds an identity to exactly one netuid, so the compose file starts pylon with one identity per
subnet (`sn<netuid>`), all backed by the same wallet.

### 10.1 Enroll the validator hotkey (chain machine)

On **`refinery-chain`**, register a dedicated validator hotkey on every subnet from netuid 3 up and give
it a small stake (a validator permit needs a non-zero stake):

```bash
mkdir -p ~/refinery-chain/multi-validator && cd ~/refinery-chain/multi-validator
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/multi-validator/enroll.py -o enroll.py
ENROLL_SUBTENSOR_NETWORK=ws://10.0.0.10:9944 ENROLL_WALLET_DIR="$HOME/refinery-chain/wallets" \
  uv run enroll.py
```

It creates the `multi-validator` wallet next to the bootstrap wallets (the coldkey stays on this machine),
funds it from `//Alice`, registers and stakes 1 TAO on each subnet (~70 s per subnet), and finally
prints the `NETUIDS=...` line for both `.env` files below. The stake is deliberately tiny: staked TAO is
swapped into the subnet's alpha pool, and these pools hold only a few thousand TAO, so a large stake
would move the subnets' prices. The hotkey gets its validator permit at each subnet's next epoch. Like
bootstrap, creating the wallet prints its mnemonics — devnet keys of no value.

Copy the public coldkey and the hotkey to the validator machine, as in §3:

```bash
# on refinery-chain
cd ~/refinery-chain/wallets && tar czf /tmp/multi-validator-wallet.tgz multi-validator/coldkeypub.txt multi-validator/hotkeys/
# on refinery-multi-validator, after copying the archive over
mkdir -p ~/.bittensor/wallets && tar xzf multi-validator-wallet.tgz -C ~/.bittensor/wallets
```

### 10.2 Install the validator

On **`refinery-multi-validator`**:

```bash
mkdir -p ~/refinery-multi-validator && cd ~/refinery-multi-validator
curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/multi-validator/.env.example -o .env
# Edit .env: NETUIDS (from enroll.py), CHAIN_VLAN_IP, VALIDATOR_VLAN_IP and the two pylon tokens
nano .env && chmod 600 .env

curl -fsSL https://raw.githubusercontent.com/backend-developers-ltd/refinery/refs/heads/deploy-config-prod/deploy/linode/install.sh \
  | bash -s -- multi-validator prod ~/refinery-multi-validator
```

The validator advertises `http://<VALIDATOR_VLAN_IP>:8001` as its callback URL and the compose file
publishes `8001` on that IP only; open it to the miner machine.

### 10.3 Point the miner at the same subnets

On **`refinery-miner`**, set `NETUIDS` in `~/refinery-miner/.env` to the same list and run
`docker compose up -d`. On its first start the miner registers on every subnet (~40 s each) before its
HTTP server comes up; restarts skip subnets it is already registered on. If the miner wallet is shared
with other tools, give this miner its own wallet with a distinct `MINER_NAME`.

### 10.4 Verify

- `docker compose logs -f validator` on `refinery-multi-validator`: `pow.solution netuid=<n> ...` lines,
  then once per epoch and subnet `Weights computed for subnet <n> epoch ...` and
  `weights.set committed netuid=<n>`.
- Right after every (re)start all the epoch clocks fire at once, before any challenge has been answered,
  so pylon rejects one empty weight set per subnet (`pipeline.error ... No weights provided`). Real weights
  follow at each subnet's next epoch boundary, and with commit-reveal they show up on chain one epoch
  later (see §6).
- On-chain weights: the §6 snippet with the subnet's netuid instead of `2`.

### 10.5 New subnets and caveats

- **New subnets.** Re-run `enroll.py` (idempotent: it enrolls only what is missing), put the new
  `NETUIDS` line into both `.env` files and run `docker compose up -d` on both machines.
- **Memory.** Nexus keeps every pipeline context in memory for the life of the process, so the validator
  grows slowly; its 768 MB `mem_limit` turns that into an automatic container restart, which costs the
  current epoch's results (see the empty weights above).
