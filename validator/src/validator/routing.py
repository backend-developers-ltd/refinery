"""Miner selection for the PoW task's neuron router.

The router hands every neuron the filter returns to the HTTP communicator, so the
filter must exclude anything that communicator cannot reach. Only neurons that actually serve a
reachable HTTP axon can answer a challenge — a validator permit is not a disqualifier, since a
productive miner can accumulate enough stake to earn one while still serving as a miner.
Registered-but-unserved neurons — the validator's own hotkey, the subnet owner, offline miners —
carry a zeroed axon (``ip=0.0.0.0``, ``port=0``, ``protocol=TCP``); routing a challenge to one
makes ``AsyncHttpNeuronCommunicator`` raise ``UnsupportedAxonProtocolException`` and burns the
block's only attempt. This filter keeps them out by mirroring the communicator's own target
requirements.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import override

from nexus.v1 import (
    Actor,
    ActorBuilder,
    AxonProtocol,
    Context,
    ContextStore,
    NetUid,
    Neuron,
    NeuronFilter,
    NeuronRouter,
    NoRoutableNeuronsException,
    PipeToBus,
    PylonClientProvider,
    Routed,
    TransformActor,
)

from validator.proof_of_work import PowChallenge

MIN_AXON_PORT = 1
MAX_AXON_PORT = 65535


def servable_http_miners(neurons: Sequence[Neuron]) -> Sequence[Neuron]:
    """Return the neurons that serve a reachable HTTP axon on a valid port."""
    return [
        neuron
        for neuron in neurons
        if neuron.axon_info.is_serving
        and neuron.axon_info.protocol == AxonProtocol.HTTP
        and MIN_AXON_PORT <= int(neuron.axon_info.port) <= MAX_AXON_PORT
    ]


class SubnetNeuronRouter(NeuronRouter[PowChallenge], ActorBuilder):
    """Routes each challenge to a miner of the challenge's own subnet.

    The stock routers serve one fixed netuid; here the subnet comes from every PowChallenge, so one
    task (and one callback port) covers all subnets. A subnet's miners are taken in hotkey order, one
    per challenge the subnet receives: the visit number derives from the challenge's block, so the
    rotation needs no stored state and every miner is sampled even with few challenges per epoch.

    sink input: PowChallenge to route
    source routed: the challenge wrapped in Routed with the selected miner
    source error: routing failures (e.g. no servable miners on the subnet)
    """

    netuids: tuple[NetUid, ...]

    def __init__(
        self,
        _id: str,
        *,
        netuids: Sequence[NetUid],
        neuron_filter: NeuronFilter,
        pylon_client_provider: PylonClientProvider | None = None,
    ) -> None:
        # The base router's fixed netuid is never read: the subnet comes from each challenge.
        super().__init__(_id, netuid=0, pylon_client_provider=pylon_client_provider, neuron_filter=neuron_filter)
        self.netuids = tuple(netuids)

    def select_miner(self, challenge: PowChallenge, neurons: Sequence[Neuron]) -> Neuron:
        """Pick the miner for the challenge among its subnet's neurons.

        Raises:
            NoRoutableNeuronsException: If no neuron passes the router's filter.

        """
        miners = sorted(self.neuron_filter(neurons), key=lambda neuron: neuron.hotkey)
        if not miners:
            raise NoRoutableNeuronsException(f"No servable miners on subnet {challenge.netuid} in {self.id}")
        subnet_visit = challenge.block_number // len(self.netuids)
        return miners[subnet_visit % len(miners)]

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return SubnetNeuronRouterActor(spec=self, pipe_to_bus=pipe_to_bus, context_store=context_store)


class SubnetNeuronRouterActor(TransformActor[PowChallenge, Routed[PowChallenge]]):
    """Actor that picks the miner for each challenge from its subnet's recent neurons."""

    router_spec: SubnetNeuronRouter

    def __init__(self, *, spec: SubnetNeuronRouter, pipe_to_bus: PipeToBus, context_store: ContextStore) -> None:
        super().__init__(spec=spec, pipe_to_bus=pipe_to_bus, context_store=context_store)
        self.router_spec = spec

    @override
    def _transform(self, ctx: Context, payload: PowChallenge) -> Routed[PowChallenge]:
        with self.router_spec.pylon_client_provider.get_client() as pylon:
            neurons = list(pylon.open_access.get_recent_neurons(payload.netuid).neurons.values())
        return Routed(input=payload, target=self.router_spec.select_miner(payload, neurons))
