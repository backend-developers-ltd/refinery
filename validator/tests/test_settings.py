from __future__ import annotations

import pytest
from nexus.v1 import NetUid
from pydantic import ValidationError
from pylon_client.artanis import IdentityName, PylonAuthToken

from validator.pylon import PylonIdentity
from validator.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def pylon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_PYLON_SERVICE_ADDRESS", "http://pylon:8000")
    monkeypatch.setenv("VALIDATOR_PYLON_OPEN_ACCESS_TOKEN", "open-token")
    monkeypatch.setenv("VALIDATOR_PYLON_IDENTITY_TOKEN", "identity-token")


def test_runs_on_every_listed_subnet_with_its_own_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_NETUIDS", "3, 5,6")
    monkeypatch.setenv("VALIDATOR_PYLON_IDENTITY_NAME", "sn{netuid}")

    settings = _settings()

    assert [settings.pylon_identity(netuid) for netuid in settings.netuids] == [
        PylonIdentity(name=IdentityName(f"sn{netuid}"), token=PylonAuthToken("identity-token")) for netuid in (3, 5, 6)
    ]


def test_single_netuid_keeps_a_plain_identity_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETUID", "2")
    monkeypatch.setenv("VALIDATOR_PYLON_IDENTITY_NAME", "validator")

    settings = _settings()

    assert (settings.netuids, settings.pylon_identity(NetUid(2))) == (
        (NetUid(2),),
        PylonIdentity(name=IdentityName("validator"), token=PylonAuthToken("identity-token")),
    )


def test_rejects_subnets_sharing_one_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_NETUIDS", "3,5")
    monkeypatch.setenv("VALIDATOR_PYLON_IDENTITY_NAME", "validator")

    with pytest.raises(ValidationError, match="own pylon identity"):
        _settings()


def test_requires_a_pylon_identity_to_set_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_NETUIDS", "3")
    monkeypatch.delenv("VALIDATOR_PYLON_IDENTITY_TOKEN")

    with pytest.raises(ValidationError, match="required to set weights"):
        _settings()
