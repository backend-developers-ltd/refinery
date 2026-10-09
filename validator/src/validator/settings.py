"""Runtime configuration of the Refinery validator (all knobs are ``VALIDATOR_*`` env vars)."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Self

from nexus.v1 import NetUid, PylonClientSettingsMixin
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pylon_client.artanis import IdentityName, PylonAuthToken

from validator.pylon import PylonIdentity

NETUID_PLACEHOLDER = "{netuid}"


class Settings(PylonClientSettingsMixin, BaseSettings):
    """Runtime configuration for the Refinery validator.

    The validator runs on every subnet in ``netuids`` (a comma-separated list; the single-subnet
    ``NETUID`` still works). Weights are set through one pylon identity per subnet, named by
    ``VALIDATOR_PYLON_IDENTITY_NAME`` with ``{netuid}`` substituted (e.g. ``sn{netuid}``) and all
    sharing ``VALIDATOR_PYLON_IDENTITY_TOKEN``; a plain name is enough for a single subnet.
    """

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_", extra="ignore")

    netuids: Annotated[tuple[NetUid, ...], NoDecode] = Field(
        min_length=1,
        validation_alias=AliasChoices("VALIDATOR_NETUIDS", "NETUIDS", "VALIDATOR_NETUID", "NETUID"),
    )
    callback_host: str = "127.0.0.1"
    callback_port: int = 8001

    difficulty: int = 16
    challenge_deadline: timedelta = timedelta(seconds=10)
    send_timeout: timedelta = timedelta(seconds=2)
    max_in_flight: int = 16
    max_attempts: int = 1

    speed_weight: float = 0.25
    target_latency: timedelta = timedelta(seconds=2)
    weight_set_delay_blocks: int = 0

    @field_validator("netuids", mode="before")
    @classmethod
    def _split_netuids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(netuid.strip() for netuid in value.split(","))
        return value

    @model_validator(mode="after")
    def _check_pylon_identities(self) -> Self:
        identity_names = {self.pylon_identity(netuid).name for netuid in self.netuids}
        if len(identity_names) != len(self.netuids):
            raise ValueError(
                f"Each subnet needs its own pylon identity: list every netuid once and put {NETUID_PLACEHOLDER} "
                f"in VALIDATOR_PYLON_IDENTITY_NAME (got {self.pylon_identity_name!r} for netuids {self.netuids})"
            )
        return self

    def pylon_identity(self, netuid: NetUid) -> PylonIdentity:
        """Return the pylon identity that sets weights on the subnet.

        Raises:
            ValueError: If the pylon identity name or token is not configured.

        """
        if self.pylon_identity_name is None or self.pylon_identity_token is None:
            raise ValueError(
                "VALIDATOR_PYLON_IDENTITY_NAME and VALIDATOR_PYLON_IDENTITY_TOKEN are required to set weights"
            )
        return PylonIdentity(
            name=IdentityName(self.pylon_identity_name.replace(NETUID_PLACEHOLDER, str(netuid))),
            token=PylonAuthToken(self.pylon_identity_token),
        )
