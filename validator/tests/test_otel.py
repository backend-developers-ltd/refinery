from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from validator.otel import OtelSettings, add_otel_context_to_structlog, add_otel_resource_to_structlog


def _settings(**overrides: object) -> OtelSettings:
    return OtelSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_traces_disabled_without_endpoint() -> None:
    assert _settings().traces_enabled is False


def test_traces_disabled_when_sdk_disabled_even_with_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318")
    monkeypatch.setenv("VALIDATOR_OTEL_SDK_DISABLED", "true")
    settings = _settings()
    assert settings.traces_enabled is False


def test_traces_enabled_with_endpoint_and_sdk_not_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318")
    settings = _settings()
    assert settings.traces_enabled is True


def test_resource_attributes_omit_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATOR_OTEL_NETUID", "")
    monkeypatch.setenv("VALIDATOR_OTEL_BITTENSOR_NETWORK", "")
    settings = _settings(service_version="")
    attrs = settings.resource_attributes()
    assert "service.version" not in attrs
    assert "bittensor.netuid" not in attrs
    assert "bittensor.network" not in attrs
    assert attrs["service.namespace"] == "sn1-refinery"
    assert attrs["service.name"] == "validator"


def test_resource_attributes_never_carry_a_hotkey() -> None:
    settings = _settings()
    assert not any("hotkey" in key for key in settings.resource_attributes())


def test_add_otel_context_to_structlog_noop_without_active_span() -> None:
    event_dict = add_otel_context_to_structlog(None, "info", {"event": "hi"})
    assert "trace_id" not in event_dict
    assert "span_id" not in event_dict


def test_add_otel_context_to_structlog_stamps_active_span() -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("test-span"):
        event_dict = add_otel_context_to_structlog(None, "info", {"event": "hi"})

    assert "trace_id" in event_dict
    assert "span_id" in event_dict


def test_add_otel_resource_to_structlog_merges_resource_and_event() -> None:
    event_dict = add_otel_resource_to_structlog(None, "info", {"event": "hi"})

    assert event_dict["event"] == "hi"
    assert event_dict["service.name"] == "validator"


def test_no_op_tracer_context_is_invalid() -> None:
    """Sanity check the assumption `add_otel_context_to_structlog` relies on when tracing is disabled."""
    assert trace.get_current_span().get_span_context().is_valid is False
