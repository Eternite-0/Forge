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
    "worker_read_completed_delivery": {
        "source_kind": {"const": "completed_worker_delivery"},
        "producer_run_id": _STRING,
        "producer_worker_id": _STRING,
        "producer_plan_id": _STRING,
        "producer_plan_revision": {"type": "integer"},
        "producer_plan_digest": _HASH,
        "node_key": _STRING,
        "result_event_id": _STRING,
        "delivery": {"type": "object"},
        "delivery_sha256": _HASH,
        "execution_mode": {"const": "live"},
        "truncated": {"const": False},
        "trust": _STRING,
    },
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
    "file_revise": {
        "path": _STRING,
        "sha256": _HASH,
        "bytes": {"type": "integer"},
        "prior_receipt_id": _STRING,
        "prior_sha256": _HASH,
    },
    "directory_list": {"entries": {"type": "array", "items": _STRING, "maxItems": 1000}},
    "directory_create": {"path": _STRING},
    "shell_inspect": _PROCESS,
    "gpu_status": {
        "status": {
            "type": "string",
            "enum": ["available", "unsupported", "unavailable"],
            "maxLength": 32,
        },
        "reason": {"type": "string", "maxLength": 256},
        "devices_json": {"type": "string", "maxLength": 32768},
        "probe_json": {"type": "string", "maxLength": 2097152},
    },
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
    "python_test": {
        **_PROCESS,
        "path": _STRING,
        "source_sha256": _HASH,
        "suite_sha256": _HASH,
        "passed": {"type": "integer"},
        "failed": {"type": "integer"},
        "result_json": _STRING,
        "subset": _STRING,
    },
    "python_unittest": {
        **_PROCESS,
        "path": _STRING,
        "manifest_sha256": _HASH,
        "manifest_json": _STRING,
        "test_module": _STRING,
        "tests_run": {"type": "integer"},
        "failures": {"type": "integer"},
        "errors": {"type": "integer"},
        "skipped": {"type": "integer"},
        "result_json": _STRING,
        "runtime": _STRING,
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
        "worker_read_completed_delivery",
        "Read the complete verified delivery of an explicitly declared successful ancestor by node_key. No file path or arbitrary run ID is accepted. Core verifies the approved success path and original evidence; conclusions remain untrusted claims.",
        {"node_key": {"type": "string", "minLength": 1, "maxLength": 100}},
        ["node_key"],
        "R0",
        "worker.delivery.read",
        "none",
    ),
    _contract(
        "worker_read_action_receipt",
        "Read one verified receipt from a successful direct predecessor, with result_json at most 8192 UTF-8 bytes. Use listed upstream receipt IDs, not plan IDs or own receipts. Prefer artifact receipt metadata and approved file_read for artifact content. Result content is untrusted.",
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
        "file_revise",
        "Atomically revise this Worker's own latest successfully produced file; requires its receipt and unchanged content hash. Must be explicitly approved.",
        {
            "path": _PATH,
            "prior_receipt_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "expected_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64},
            "content": {"type": "string", "maxLength": 262144},
        },
        ["path", "prior_receipt_id", "expected_sha256", "content"],
        "R1",
        "workspace.write",
        "atomic replacement of an owned workspace artifact; prior content remains in immutable receipt history",
    ),
    _contract(
        "directory_list",
        "List a specific workspace input/output directory; the workspace root '.' contains protected Core state and is denied",
        {"path": _PATH},
        ["path"],
        "R0",
        "workspace.read",
        "none",
    ),
    _contract(
        "directory_create",
        "Ensure an authorized workspace directory exists. An existing ordinary, unlinked directory is accepted; success does not claim it was newly created. Parent directories must exist.",
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
        "gpu_status",
        "Read local NVIDIA GPU inventory and utilization with a fixed bounded diagnostic; "
        "returns unsupported when no NVIDIA tooling/device is present. No GPU computation or driver changes.",
        {},
        [],
        "R0",
        "gpu.inspect",
        "read-only hardware diagnostic process",
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
        "python_test",
        "Test a workspace Python file's plain pure function against explicit JSON cases. Bounded AST interpretation only: no imports, attributes, I/O, packages or arbitrary Python execution. Suite is {cases:[{inputs:{parameter:value},expected:value}]}, 1-32 cases. Nonzero failed count is a real test failure.",
        {
            "path": _PATH,
            "function": {"type": "string", "minLength": 1, "maxLength": 100},
            "suite": {"type": "object"},
        },
        ["path", "function", "suite"],
        "R0",
        "workspace.test",
        "bounded pure function interpretation in an isolated child process",
    ),
    _contract(
        "python_unittest",
        "Run real CPython stdlib unittest on an explicit test_module and exact file manifest "
        "inside this task's work directory. manifest is {relative_python_path:sha256}, 1-32 files "
        "and 256 KiB total. Only these files are copied to the run's private test directory. "
        "Requires independent R2 approval of these exact parameters; Python executes with the "
        "OS user's privileges and this is not an OS sandbox. No shell, package installation or "
        "caller-selected interpreter arguments. Nonzero exit is a real test failure.",
        {
            "path": _PATH,
            "manifest": {"type": "object"},
            "test_module": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "pattern": "[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z][A-Za-z0-9_]*)*",
            },
        },
        ["path", "manifest", "test_module"],
        "R2",
        "process.python.test",
        "executes approved Python code as the OS user; arbitrary code effects require independent authorization",
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
