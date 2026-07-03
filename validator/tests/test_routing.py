from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import cast

from nexus.v1 import AxonProtocol, Neuron, Port
from pylon_client.artanis.v1 import AxonInfo

from validator.routing import servable_http_miners


@dataclass(frozen=True)
class _Neuron:
    hotkey: str
    validator_permit: bool
    axon_info: AxonInfo


def _neuron(
    hotkey: str,
    *,
    validator_permit: bool = False,
    ip: str = "10.0.0.30",
    port: int = 18000,
    protocol: AxonProtocol = AxonProtocol.HTTP,
) -> _Neuron:
    return _Neuron(
        hotkey=hotkey,
        validator_permit=validator_permit,
        axon_info=AxonInfo(ip=IPv4Address(ip), port=Port(port), protocol=protocol),
    )


def _filter(neurons: Sequence[_Neuron]) -> Sequence[Neuron]:
    return servable_http_miners(cast(Sequence[Neuron], neurons))


def test_keeps_servable_http_miners_including_permit_holders() -> None:
    permitted_miner = _neuron("permitted-miner", validator_permit=True)
    honest_a = _neuron("honest-a")
    honest_b = _neuron("honest-b", ip="10.0.0.31", port=18001)
    neurons = [
        permitted_miner,
        _neuron("validator-self", ip="0.0.0.0", port=0, protocol=AxonProtocol.TCP),
        _neuron("owner-unserved", ip="0.0.0.0", port=0, protocol=AxonProtocol.TCP),
        honest_a,
        _neuron("miner-no-port", port=0),
        _neuron("miner-udp", protocol=AxonProtocol.UDP),
        honest_b,
    ]

    assert _filter(neurons) == [permitted_miner, honest_a, honest_b]


def test_empty_input_returns_empty() -> None:
    assert _filter([]) == []
