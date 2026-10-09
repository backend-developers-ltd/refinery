"""Pylon clients bound to one pylon identity.

A pylon identity pairs a wallet with exactly one netuid, and weights are set through identity
endpoints only. A validator running on several subnets therefore talks to pylon as a different identity
per subnet, while everything else (block clocks, neuron lookups, miner traffic) uses the identity-less
default provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from nexus.v1 import PylonClientProvider, PylonClientSettingsMixin, SyncPylonClientLike
from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient


@dataclass(frozen=True)
class PylonIdentity:
    """A pylon identity the validator authenticates as."""

    name: IdentityName
    token: PylonAuthToken


class IdentityPylonClientProvider(PylonClientProvider):
    """Builds Pylon clients that act as the given identity, configured like the validator's default clients."""

    settings: PylonClientSettingsMixin
    identity: PylonIdentity

    def __init__(self, settings: PylonClientSettingsMixin, identity: PylonIdentity) -> None:
        self.settings = settings
        self.identity = identity

    @override
    def get_client(self) -> SyncPylonClientLike:
        return PylonClient(
            Config(
                address=self.settings.pylon_service_address,
                open_access_token=PylonAuthToken(self.settings.pylon_open_access_token),
                identity_name=self.identity.name,
                identity_token=self.identity.token,
                mtls_cert_path=self.settings.mtls_cert_path,
                mtls_key_path=self.settings.mtls_key_path,
                neurons_file=self.settings.neurons_file,
                neuron_keepalive_expiry=self.settings.neuron_keepalive_expiry,
            )
        )
