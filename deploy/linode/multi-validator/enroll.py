# /// script
# requires-python = ">=3.14"
# dependencies = [
#     # Same pins as localnet/bootstrap.py: pylon cannot parse keyfiles written by bittensor-wallet 4.1.0+.
#     "bittensor==10.3.0",
#     "bittensor-wallet==4.0.1",
# ]
# ///
"""
Enroll the multi-subnet validator on every subnet from ENROLL_MIN_NETUID upwards.

For each such subnet the validator hotkey is registered (burned registration) and given a small stake,
so the chain grants it a validator permit at the next epoch. The coldkey never leaves this machine; copy
only `coldkeypub.txt` + `hotkeys/` to the validator machine. Prints the NETUIDS line for the validator's
and the miner's `.env`.

The stake is deliberately tiny: every TAO staked is swapped into the subnet's alpha pool and moves its
price, and these pools hold only a few thousand TAO. A validator permit only needs a non-zero stake while
the subnet has fewer neurons than permit slots.

Run it again after new subnets are created: it is idempotent and enrolls only what is missing.

Config (environment variables):
- ENROLL_SUBTENSOR_NETWORK: subtensor ws endpoint (default ws://127.0.0.1:9944)
- ENROLL_WALLET_DIR: directory holding the validator wallet (default ./wallets)
- ENROLL_WALLET_NAME: validator wallet name, created if missing (default multi-validator)
- ENROLL_MIN_NETUID: lowest subnet to enroll on (default 3)
- ENROLL_STAKE_TAO: TAO staked on each subnet where the validator has no stake yet (default 1)

Usage: uv run enroll.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import bittensor as bt
from bittensor.utils.balance import Balance
from bittensor_wallet import Keypair, Wallet

SUBTENSOR_NETWORK = os.environ.get("ENROLL_SUBTENSOR_NETWORK", "ws://127.0.0.1:9944")
WALLETS_DIR = Path(os.environ.get("ENROLL_WALLET_DIR", "wallets"))
WALLET_NAME = os.environ.get("ENROLL_WALLET_NAME", "multi-validator")
MIN_NETUID = int(os.environ.get("ENROLL_MIN_NETUID", "3"))
STAKE_TAO = float(os.environ.get("ENROLL_STAKE_TAO", "1"))

FUND_AMOUNT_TAO = 100.0
MIN_BALANCE_PER_SUBNET_TAO = 2.0


def get_alice_wallet() -> Wallet:
    """Create a wallet backed by Alice's well-known devnet keypair."""
    alice_kp = Keypair.create_from_uri("//Alice")
    wallet = Wallet(name="alice", path=str(WALLETS_DIR))
    wallet.set_coldkey(keypair=alice_kp, encrypt=False, overwrite=True)
    wallet.set_coldkeypub(keypair=alice_kp, overwrite=True)
    wallet.set_hotkey(keypair=alice_kp, encrypt=False, overwrite=True)
    return wallet


def fund_wallet(subtensor: bt.Subtensor, wallet: Wallet, subnet_count: int) -> None:
    """Top the validator coldkey up from Alice when it cannot cover registration and stake everywhere."""
    balance = subtensor.get_balance(wallet.coldkey.ss58_address)
    if balance >= Balance.from_tao(subnet_count * MIN_BALANCE_PER_SUBNET_TAO):
        print(f"{wallet.name} balance {balance} is sufficient")
        return
    print(f"Funding {wallet.name} with {FUND_AMOUNT_TAO} TAO from Alice...")
    response = subtensor.transfer(
        wallet=get_alice_wallet(),
        destination_ss58=wallet.coldkey.ss58_address,
        amount=Balance.from_tao(FUND_AMOUNT_TAO),
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        sys.exit(f"Transfer failed: {response.message}")


def register(subtensor: bt.Subtensor, wallet: Wallet, netuid: int) -> None:
    """Register the validator hotkey on the subnet unless it already is."""
    if subtensor.is_hotkey_registered(wallet.hotkey.ss58_address, netuid):
        print(f"  already registered on subnet {netuid}")
        return
    response = subtensor.burned_register(
        wallet=wallet,
        netuid=netuid,
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        sys.exit(f"Registration on subnet {netuid} failed: {response.message}")
    print(f"  registered on subnet {netuid}")


def stake(subtensor: bt.Subtensor, wallet: Wallet, netuid: int) -> None:
    """Stake STAKE_TAO on the validator hotkey in the subnet unless it already holds some stake there."""
    current = subtensor.get_stake(
        coldkey_ss58=wallet.coldkey.ss58_address,
        hotkey_ss58=wallet.hotkey.ss58_address,
        netuid=netuid,
    )
    if current.tao > 0:
        print(f"  already staked on subnet {netuid} ({current})")
        return
    response = subtensor.add_stake(
        wallet=wallet,
        netuid=netuid,
        hotkey_ss58=wallet.hotkey.ss58_address,
        amount=Balance.from_tao(STAKE_TAO),
        wait_for_inclusion=True,
        wait_for_finalization=True,
        mev_protection=False,
    )
    if not response.success:
        sys.exit(f"Staking on subnet {netuid} failed: {response.message}")
    print(f"  staked {STAKE_TAO} TAO on subnet {netuid}")


def main() -> None:
    """Enroll the validator on every subnet from MIN_NETUID upwards and print their netuids."""
    subtensor = bt.Subtensor(network=SUBTENSOR_NETWORK)
    netuids = sorted(netuid for netuid in subtensor.get_all_subnets_netuid() if netuid >= MIN_NETUID)
    if not netuids:
        sys.exit(f"No subnets with netuid >= {MIN_NETUID} on {SUBTENSOR_NETWORK}")

    wallet = Wallet(name=WALLET_NAME, path=str(WALLETS_DIR))
    wallet.create_if_non_existent(coldkey_use_password=False, hotkey_use_password=False)
    print(f"Validator hotkey: {wallet.hotkey.ss58_address}")
    fund_wallet(subtensor, wallet, len(netuids))

    for netuid in netuids:
        print(f"Subnet {netuid}:")
        register(subtensor, wallet, netuid)
        stake(subtensor, wallet, netuid)

    print("\nEnrolled. Put this line in the validator's and the miner's .env:")
    print(f"NETUIDS={','.join(str(netuid) for netuid in netuids)}")


if __name__ == "__main__":
    main()
