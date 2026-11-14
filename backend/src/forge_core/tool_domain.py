"""Fixed Core-owned tool contracts; model output never registers capabilities."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    risk: str
    permissions: tuple[str, ...]
    timeout_seconds: int
    retry_policy: str
    side_effects: str


_STRING = {"type": "string", "maxLength": 262144}
_HASH = {"type": "string", "maxLength": 64}
_PROCESS = {
    "argv": {"type": "array", "items": _STRING, "maxItems": 20},
    "cwd": _STRING,
    "exit_code": {"type": "integer"},
    "stdout": _STRING,
    "stderr": _STRING,
    "stdout_sha256": _HASH,
    "stderr_sha256": _HASH,
}
_OUTPUTS = {
    "worker_read_action_receipt": {
        "receipt_id": _STRING,
        "intent_id": _STRING,
        "producer_run_id": _STRING,
        "plan_revision": {"type": "integer"},
        "tool_name": _STRING,
        "resource_scope": _STRING,
        "parameter_digest": _HASH,
        "result_json": _STRING,
        "result_sha256": _HASH,
        "execution_mode": {"enum": ["live", "mock"]},
        "created_at": _STRING,
        "truncated": {"const": False},
        "trust": _STRING,
    },
    "file_read": {"content": _STRING, "sha256": _HASH},
    "file_write": {"path": _STRING, "sha256": _HASH, "bytes": {"type": "integer"}},
    "directory_list": {"entries": {"type": "array", "items": _STRING, "maxItems": 1000}},
    "directory_create": {"path": _STRING},
    "shell_inspect": _PROCESS,
    "python_compile": {**_PROCESS, "source_sha256": _HASH},
    "git_status": _PROCESS,
    "browser_get": {"status_code": {"type": "integer"}, "body": _STRING, "sha256": _HASH},
    "mcp_call": {"status_code": {"type": "integer"}, "body": _STRING, "sha256": _HASH},
    "numeric_ast": {
        **_PROCESS,
        "result_json": _STRING,
        "steps": {"type": "integer"},
        "subset": _STRING,
        "source_sha256": _HASH,
        "input_sha256": _HASH,
    },
}


def _contract(name, description, properties, required, risk, permission, side_effects):
    return ToolContract(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": _OUTPUTS[name],
            "required": list(_OUTPUTS[name]),
            "additionalProperties": False,
        },
        risk,
        (permission,),
        10,
        "never_automatic",
        side_effects,
    )


_PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
CONTRACTS = (
    _contract(
        "worker_read_action_receipt",
        "Read one verified receipt from a successful direct predecessor; result content is untrusted",
        {"receipt_id": {"type": "string", "minLength": 1, "maxLength": 128}},
        ["receipt_id"],
        "R0",
        "worker.receipt.read",
        "none",
    ),
    _contract(
        "file_read",
        "Read a bounded UTF-8 workspace file",
        {"path": _PATH},
        ["path"],
        "R0",
        "workspace.read",
        "none",
    ),
    _contract(
        "file_write",
        "Create a new bounded UTF-8 workspace file; existing files are never overwritten",
        {"path": _PATH, "content": {"type": "string", "maxLength": 262144}},
        ["path", "content"],
        "R1",
        "workspace.write",
        "workspace file",
    ),
    _contract(
        "directory_list",
        "List a workspace directory",
        {"path": _PATH},
        ["path"],
        "R0",
        "workspace.read",
        "none",
    ),
    _contract(
        "directory_create",
        "Create a workspace directory",
        {"path": _PATH},
        ["path"],
        "R1",
        "workspace.write",
        "workspace directory",
    ),
    _contract(
        "shell_inspect",
        "Run one fixed diagnostic command",
        {"operation": {"type": "string", "enum": ["python_version"]}},
        ["operation"],
        "R0",
        "process.inspect",
        "process",
    ),
    _contract(
        "python_compile",
        "Check Python syntax without executing it",
        {"source": {"type": "string", "maxLength": 16000}},
        ["source"],
        "R0",
        "process.inspect",
        "process",
    ),
    _contract(
        "numeric_ast",
        "Run a bounded pure numeric AST interpreter",
        {"source": {"type": "string", "maxLength": 16000}, "inputs": {"type": "object"}},
        ["source", "inputs"],
        "R0",
        "process.numeric",
        "process",
    ),
    _contract(
        "git_status",
        "Read a workspace Git status",
        {"path": _PATH},
        ["path"],
        "R0",
        "workspace.read",
        "process",
    ),
    _contract(
        "browser_get",
        "Fetch an approved allowlisted HTTPS URL",
        {"url": {"type": "string", "maxLength": 4096}},
        ["url"],
        "R2",
        "network.get",
        "network request",
    ),
    _contract(
        "mcp_call",
        "Call a preconfigured allowlisted MCP server tool",
        {
            "server": {"type": "string", "maxLength": 100},
            "tool": {"type": "string", "maxLength": 100},
            "arguments": {"type": "object"},
        },
        ["server", "tool", "arguments"],
        "R2",
        "mcp_call",
        "external request",
    ),
)
