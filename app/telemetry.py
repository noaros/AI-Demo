"""Tracing: every request becomes one trace in Application Insights.

A trace is a tree of *spans*, one per piece of work, each with a start, a duration and attributes:

    POST /chat                                   (FastAPI, automatic)
    ├── content_safety shieldPrompt              (ours, app/safety.py)
    ├── Cosmos DB read                           (Azure SDK, automatic)
    └── invoke_agent contoso-helpdesk            (Agent Framework)
        ├── chat gpt-5-mini                      (one per model call, with token counts)
        ├── execute_tool get_ticket              (one per tool call)
        │   └── content_safety shieldPrompt      (the tool result check)
        └── chat gpt-5-mini

It's all OpenTelemetry, the vendor-neutral standard: Application Insights is just where it's sent.
"""

import os
from contextvars import ContextVar

from azure.core.credentials import TokenCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from agent_framework.observability import enable_instrumentation
from opentelemetry import trace

tracer = trace.get_tracer("contoso-helpdesk")

# The request's own span (POST /chat), remembered by middleware in main.py. Inside an endpoint the *current*
# span is FastAPI's inner "fastapi.endpoint" span, and attributes there don't show up on the request.
request_span: ContextVar[trace.Span | None] = ContextVar("request_span", default=None)


def setup(connection_string: str | None, credential: TokenCredential) -> None:
    if not connection_string:
        return  # e.g. running locally without Application Insights: spans are created but go nowhere
    os.environ.setdefault("OTEL_SERVICE_NAME", "contoso-helpdesk-api")  # the name Application Insights shows
    configure_azure_monitor(
        connection_string=connection_string,
        credential=credential,  # Application Insights has local auth off: telemetry is sent with Entra ID
        logger_name="agent",  # our log lines ("tool call: ...", "safety: ...") go along, linked to their trace
    )
    # Agent Framework's spans follow the OpenTelemetry GenAI conventions: agent runs, model calls with token
    # counts, tool executions. Prompts and answers are NOT recorded (enable_sensitive_data=False): they
    # contain whatever users type. Turn it on only where that's acceptable.
    enable_instrumentation(enable_sensitive_data=False)


def current_trace_id() -> str:
    """The id to look a request up by in Application Insights. Returned with every response."""
    return format(trace.get_current_span().get_span_context().trace_id, "032x")


def annotate(**attributes) -> None:
    """Adds attributes to the request's span (requests table in Application Insights), so they can be queried."""
    span = request_span.get() or trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(f"helpdesk.{key}", value)
