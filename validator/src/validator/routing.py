"""Miner selection for the PoW task's neuron router.

The round-robin router hands every neuron the filter returns to the HTTP communicator, so the
filter must exclude anything that communicator cannot reach. Lacking a validator permit marks a
neuron as a miner, but only miners that actually serve a reachable HTTP axon can answer a
challenge. Registered-but-unserved neurons — the validator's own hotkey before it earns a permit,
the subnet owner, offline miners — carry a zeroed axon (``ip=0.0.0.0``, ``port=0``,
``protocol=TCP``); routing a challenge to one makes ``AsyncHttpNeuronCommunicator`` raise
``UnsupportedAxonProtocolException`` and burns the block's only attempt. This filter keeps them out
by mirroring the communicator's own target requirements.
"""

from __future__ import annotations

from collections.abc import Sequence

from nexus.v1 import AxonProtocol, Neuron

MIN_AXON_PORT = 1
MAX_AXON_PORT = 65535


def servable_http_miners(neurons: Sequence[Neuron]) -> Sequence[Neuron]:
    """Return the non-validator neurons that serve a reachable HTTP axon on a valid port."""
    return [
        neuron
        for neuron in neurons
        if not neuron.validator_permit
        and neuron.axon_info.is_serving
        and neuron.axon_info.protocol == AxonProtocol.HTTP
        and MIN_AXON_PORT <= int(neuron.axon_info.port) <= MAX_AXON_PORT
    ]
