# /// script
# requires-python = ">=3.14"
# dependencies = [
#     # Pin bittensor-wallet to pylon's version (4.0.1); 4.1.0+ writes keyfiles pylon 2.0.0 can't
#     # parse. Keep in sync with localnet/bootstrap.py and miner/uv.lock.
#     "bittensor==10.3.0",
#     "bittensor-wallet==4.0.1",
#     "litestar[standard]",
#     "httpx",
#     "click",
# ]
# ///
"""
Localnet miner fixture — honest proof-of-work brute-forcer.

Honest profile for the Refinery subnet: receives a challenge (seed + difficulty), brute-forces a
nonce whose ``SHA-256(seed || nonce)`` has enough leading zero bits, and POSTs it back to the
validator's callback URL. An optional ``--delay`` (seconds) before answering lets the same honest
profile stand in for a *slow* miner, so you can watch its weight drop relative to fast miners.

Localnet-only test fixture — never a production miner. The standalone production miner lives in /miner.

Usage:
  uv run localnet/miners/miner-honest.py [-n N] [--name NAME] [--delay SECONDS]

Examples (run several side by side to see weights spread by speed):
  uv run localnet/miners/miner-honest.py --name honest  --delay 0
  uv run localnet/miners/miner-honest.py --name slow    --delay 3
  uv run localnet/miners/miner-honest.py --name laggard --delay 15   # exceeds the validator deadline -> 0 weight
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import random
import socket
import sys
import time
from pathlib import Path
from typing import Any

import bittensor as bt
import click
import httpx
import uvicorn
from bittensor.utils.balance import Balance
from bittensor_wallet import Keypair, Wallet
from litestar import Litestar, Response, post
from litestar.background_tasks import BackgroundTask
from pydantic import BaseModel

MINER_NAME = "honest"
RESPONSE_DELAY_S = 0.0
PORT_RANGE = (10000, 65000)

# Must match the validator's AsyncHttpNeuronCommunicator target_path
TARGET_PATH = "/task"

WALLETS_DIR = Path(__file__).resolve().parent.parent / "wallets"
SUBTENSOR_NETWORK = "ws://127.0.0.1:9944"
NETUID = 2
FUND_AMOUNT_TAO = 1000.0


# ---------------------------------------------------------------------------
# Async callback protocol (matches nexus envelope types)
# ---------------------------------------------------------------------------


class RequestEnvelope(BaseModel):
    request_id: str
    callback_url: str
    input: dict[str, Any]


class ResponseEnvelope(BaseModel):
    request_id: str
    output: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Proof-of-work solving (mirrors validator/src/validator/proof_of_work.py)
# ---------------------------------------------------------------------------


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


def run_task(input_data: dict[str, Any]) -> dict[str, Any]:
    """Brute-force the challenge and return a solution matching the validator's PowSolution."""
    seed_bytes = bytes.fromhex(input_data["seed"])
    difficulty = int(input_data["difficulty"])
    counter = 0
    while True:
        nonce = str(counter)
        if leading_zero_bits(hashlib.sha256(seed_bytes + nonce.encode("utf-8")).digest()) >= difficulty:
            return {"challenge_id": input_data["challenge_id"], "nonce": nonce}
        counter += 1


# ---------------------------------------------------------------------------
# HTTP endpoint
#
# The Nexus AsyncHttpNeuronCommunicator expects a fast 2xx ACK on this path
# (within send_timeout, ~2s) and then the actual result POSTed back to
# data.callback_url. So: ACK immediately, then solve + (optionally) delay and
# POST the response envelope from the background.
# ---------------------------------------------------------------------------


async def _post_response(callback_url: str, response: ResponseEnvelope) -> None:
    async with httpx.AsyncClient() as client:
        try:
            await client.post(callback_url, json=response.model_dump())
            print(f"[miner] Responded to {response.request_id}")
        except Exception as exc:
            print(f"[miner] Failed to callback for {response.request_id}: {exc}")


async def _process_in_background(data: RequestEnvelope) -> None:
    try:
        output = await asyncio.to_thread(run_task, data.input)
        if RESPONSE_DELAY_S > 0:
            await asyncio.sleep(RESPONSE_DELAY_S)
        response = ResponseEnvelope(request_id=data.request_id, output=output)
    except Exception as exc:
        response = ResponseEnvelope(request_id=data.request_id, error=str(exc))
    await _post_response(data.callback_url, response)


@post(TARGET_PATH, status_code=202)
async def ack_task(data: RequestEnvelope) -> Response[None]:
    print(f"[miner] Received request {data.request_id}")
    return Response(
        content=None,
        status_code=202,
        background=BackgroundTask(_process_in_background, data),
    )


# ---------------------------------------------------------------------------
# Self-registration and serving
# ---------------------------------------------------------------------------


def connect_subtensor() -> bt.Subtensor:
    for attempt in range(20):
        try:
            subtensor = bt.Subtensor(network=SUBTENSOR_NETWORK)
            subtensor.get_current_block()
            return subtensor
        except Exception:
            print(f"[miner] Waiting for subtensor... ({attempt + 1}/20)")
            time.sleep(2)
    print("[miner] Could not connect to subtensor")
    sys.exit(1)


def get_alice_wallet() -> Wallet:
    """Create a wallet backed by Alice's well-known devnet keypair."""
    alice_kp = Keypair.create_from_uri("//Alice")
    wallet = Wallet(name="alice", path=str(WALLETS_DIR))
    wallet.set_coldkey(keypair=alice_kp, encrypt=False, overwrite=True)
    wallet.set_coldkeypub(keypair=alice_kp, overwrite=True)
    wallet.set_hotkey(keypair=alice_kp, encrypt=False, overwrite=True)
    return wallet


def find_free_port() -> int:
    """Find a free port by trying random ports in the range."""
    lo, hi = PORT_RANGE
    while True:
        port = random.randint(lo, hi)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port


def setup_and_serve(instance_name: str) -> None:
    """Idempotent setup then serve. Runs in its own process."""
    port = find_free_port()
    print(f"[{instance_name}] Starting on port {port} (response_delay_s={RESPONSE_DELAY_S})...")
    subtensor = connect_subtensor()

    wallet = Wallet(name=instance_name, path=str(WALLETS_DIR))
    wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)

    # Fund from Alice if needed (retry — concurrent transfers from Alice get temporarily banned)
    balance = subtensor.get_balance(wallet.coldkey.ss58_address)
    if balance < Balance.from_tao(10.0):
        alice = get_alice_wallet()
        for attempt in range(5):
            print(f"[{instance_name}] Funding from Alice... (attempt {attempt + 1}/5)")
            response = subtensor.transfer(
                wallet=alice,
                destination_ss58=wallet.coldkey.ss58_address,
                amount=Balance.from_tao(FUND_AMOUNT_TAO),
                wait_for_inclusion=True,
                wait_for_finalization=True,
                mev_protection=False,
            )
            if response.success:
                break
            print(f"[{instance_name}] Funding failed: {response.message}, retrying...")
            time.sleep(3 + attempt * 2)
        else:
            print(f"[{instance_name}] Funding failed after 5 attempts")
            sys.exit(1)

    # Register on subnet (retry — same nonce contention can happen here)
    if not subtensor.is_hotkey_registered(wallet.hotkey.ss58_address, NETUID):
        for attempt in range(5):
            print(f"[{instance_name}] Registering on subnet {NETUID}... (attempt {attempt + 1}/5)")
            response = subtensor.burned_register(
                wallet=wallet,
                netuid=NETUID,
                wait_for_inclusion=True,
                wait_for_finalization=True,
                mev_protection=False,
            )
            if response.success:
                break
            print(f"[{instance_name}] Registration failed: {response.message}, retrying...")
            time.sleep(3 + attempt * 2)
        else:
            print(f"[{instance_name}] Registration failed after 5 attempts")
            sys.exit(1)
    else:
        print(f"[{instance_name}] Already registered")

    print(f"[{instance_name}] Setting axon info: 127.0.0.2:{port}")
    subtensor.serve_axon(
        netuid=NETUID,
        axon=bt.Axon(wallet=wallet, port=port, ip="127.0.0.2", external_ip="127.0.0.2"),
    )

    print(f"[{instance_name}] Serving on 0.0.0.0:{port}{TARGET_PATH}")
    app = Litestar(route_handlers=[ack_task])
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option("-n", "count", default=1, help="Number of instances to spawn.")
@click.option("--name", "name", default=MINER_NAME, help="Profile/wallet base name.")
@click.option("--delay", "delay", default=0.0, help="Artificial seconds to wait before answering.")
def main(count: int, name: str, delay: float) -> None:
    global MINER_NAME, RESPONSE_DELAY_S
    MINER_NAME = name
    RESPONSE_DELAY_S = delay

    if count == 1:
        setup_and_serve(f"{MINER_NAME}-1")
        return

    processes: list[multiprocessing.Process] = []
    for i in range(count):
        instance_name = f"{MINER_NAME}-{i + 1}"
        proc = multiprocessing.Process(target=setup_and_serve, args=(instance_name,))
        proc.start()
        processes.append(proc)

    try:
        for proc in processes:
            proc.join()
    except KeyboardInterrupt:
        for proc in processes:
            proc.terminate()


if __name__ == "__main__":
    main()
