from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelCallContext:
    call_id: str
    purpose: str
    workspace_id: str
    quest_id: str | None = None
    worker_run_id: str | None = None
    parent_call_id: str | None = None

    def __post_init__(self):
        for value in (self.call_id, self.purpose, self.workspace_id):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError("Invalid model call context")
        for value in (self.quest_id, self.worker_run_id, self.parent_call_id):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ValueError("Invalid model call context")


@dataclass
class ModelRequest:
    model_id: str
    messages: list[dict[str, Any]]
    tools: list[dict] | None = None
    max_output_tokens: int = 2048
    provider_id: str | None = None
    response_format: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    tool_choice: str | dict | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    model_capabilities: dict[str, bool | None] | None = None
    model_capability_sources: dict[str, str] | None = None
    capability_source: str | None = None
    capability_override: bool = False
    max_retries: int | None = None
    budget_context: ModelCallContext | None = None
    context_limit: int | None = None


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[dict]
    usage: dict
    provider_id: str | None = None
    model_id: str | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    cost: dict | None = None
    content: list[dict] = field(default_factory=list)


@dataclass
class StreamEvent:
    type: str
    text: str | None = None
    data: dict | None = None
    response: ModelResponse | None = None


class ProviderAdapter(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class UnconfiguredProvider:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("No provider configured. No model call was performed.")


class ToolDefinition:
    def __init__(self, name, description, input_schema, risk="R0"):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.risk = risk


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)


class AgentLoop:
    """Legacy model-proposal helper; WorkerManager is the governed execution loop."""

    def __init__(self, provider, tools, max_steps=8):
        self.provider = provider
        self.tools = tools
        self.max_steps = max_steps

    async def run(self, model_id, messages):
        trace = []
        for step in range(self.max_steps):
            response = await self.provider.generate(
                ModelRequest(model_id=model_id, messages=messages)
            )
            trace.append({"step": step, "text": response.text, "tool_calls": response.tool_calls})
            if not response.tool_calls:
                return {"status": "model_proposal", "text": response.text, "trace": trace}
            return {"status": "policy_engine_required", "trace": trace}
        return {"status": "max_steps_reached", "trace": trace}
