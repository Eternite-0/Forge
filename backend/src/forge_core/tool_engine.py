"""Fixed, fail-closed tool execution with durable intent and receipt records."""

import asyncio
import copy
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .artifact_revision import verify_file_revision_chain
from .domain import DomainError, ForgeEvent, new_id, now_iso
from .governance import POLICY_VERSION, GovernanceService, parameter_digest
from .tool_domain import CONTRACTS

_MAX_BYTES = 262144


class ToolAuthorizationRequired(DomainError):
    """No intent/effect exists; trusted Worker may park and resume the same call."""

    def __init__(self, tool_name, parameters, resource_scope, risk):
        super().__init__("Tool authorization is required")
        self.details = {
            "action_type": tool_name,
            "tool_name": tool_name,
            "parameters": copy.deepcopy(parameters),
            "parameter_digest": parameter_digest(parameters),
            "resource_scope": resource_scope,
            "risk": risk,
            "reason": "Explicit user authorization is required before execution",
        }


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class ToolEngine:
    @classmethod
    def from_environment(cls, store):
        """Only the trusted process operator configures network allowlists."""
        try:
            hosts = json.loads(os.environ.get("FORGE_TOOL_NETWORK_HOSTS_JSON", "[]"))
            servers = json.loads(os.environ.get("FORGE_MCP_SERVERS_JSON", "{}"))
            if (
                not isinstance(hosts, list)
                or len(hosts) > 64
                or any(
                    not isinstance(host, str)
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
                    for host in hosts
                )
            ):
                raise ValueError
            if not isinstance(servers, dict) or len(servers) > 16:
                raise ValueError
            for name, config in servers.items():
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                    or not isinstance(config, dict)
                    or set(config) != {"url", "tools"}
                    or not isinstance(config["url"], str)
                    or not isinstance(config["tools"], list)
                    or not 1 <= len(config["tools"]) <= 64
                    or any(
                        not isinstance(tool, str)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tool)
                        for tool in config["tools"]
                    )
                ):
                    raise ValueError
            engine = cls(store, network_hosts=hosts, mcp_servers=servers)
            for config in servers.values():
                engine._allowed_url(config["url"])
            return engine
        except (ValueError, TypeError, DomainError):
            raise ValueError("Invalid operator tool network configuration") from None

    def __init__(self, store, *, network_hosts=(), mcp_servers=None):
        self.store = store
        self._network_hosts = frozenset(network_hosts)
        self._mcp_servers = dict(mcp_servers or {})
        self._specs = {spec.name: spec for spec in CONTRACTS}

    def contracts(self):
        return copy.deepcopy(CONTRACTS)

    def authorization_ready(self, run_id, call_id):
        """Read-only readiness; never creates intents or consumes an approval."""
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT c.*,r.quest_id,r.plan_revision,r.awaiting_authorization FROM worker_tool_calls c JOIN worker_runs r ON r.id=c.run_id WHERE c.run_id=? AND c.call_id=?",
                (run_id, call_id),
            ).fetchone()
            if (
                row is None
                or row["state"] != "pending"
                or not row["awaiting_authorization"]
                or connection.execute(
                    "SELECT 1 FROM action_intents WHERE call_id=?", (call_id,)
                ).fetchone()
            ):
                return False
            try:
                parameters = json.loads(row["arguments_json"])
                spec, _ = self._parameters(row["tool_name"], parameters)
                scope = self._scope(row["tool_name"], parameters)
                if spec.risk == "R1":
                    grants = connection.execute(
                        "SELECT resource_scope FROM task_grants WHERE quest_id=? AND plan_revision=? AND revoked_at IS NULL",
                        (row["quest_id"], row["plan_revision"]),
                    ).fetchall()
                    return any(Path(scope).is_relative_to(Path(item[0])) for item in grants)
                if spec.risk == "R2":
                    approvals = connection.execute(
                        "SELECT expires_at FROM approvals WHERE quest_id=? AND plan_revision=? AND action_type=? AND parameter_digest=? AND resource_scope=? AND risk='R2' AND policy_version=? AND status='APPROVED' AND consumed_at IS NULL AND NOT EXISTS (SELECT 1 FROM tool_approval_requests b WHERE b.approval_id=approvals.id AND b.call_id<>?)",
                        (
                            row["quest_id"],
                            row["plan_revision"],
                            row["tool_name"],
                            parameter_digest(parameters),
                            scope,
                            POLICY_VERSION,
                            call_id,
                        ),
                    ).fetchall()
                    return len(approvals) == 1 and datetime.fromisoformat(
                        approvals[0][0]
                    ) > datetime.now(UTC)
                return spec.risk == "R0"
            except (DomainError, ValueError, TypeError):
                return False

    def definitions(self, allowed_names):
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": copy.deepcopy(spec.input_schema),
            }
            for spec in CONTRACTS
            if spec.name in allowed_names
        ]

    def _parameters(self, name, parameters):
        spec = self._specs.get(name)
        if spec is None or not isinstance(parameters, dict):
            raise DomainError("Unknown tool or invalid parameters")
        properties = spec.input_schema["properties"]
        if set(parameters) != set(spec.input_schema["required"]):
            raise DomainError("Invalid tool parameters")
        for key, value in parameters.items():
            rule = properties[key]
            if rule["type"] == "string":
                if (
                    not isinstance(value, str)
                    or not value
                    or len(value) > rule.get("maxLength", 4096)
                    or "\x00" in value
                ):
                    raise DomainError("Invalid tool parameter")
                if "enum" in rule and value not in rule["enum"]:
                    raise DomainError("Unknown diagnostic operation")
                if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
                    raise DomainError("Invalid tool parameter")
            elif rule["type"] == "object" and not isinstance(value, dict):
                raise DomainError("Invalid tool parameter")
        try:
            encoded = _json(parameters)
        except (TypeError, ValueError) as exc:
            raise DomainError("Invalid JSON parameters") from exc
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise DomainError("Tool parameters exceed size limit")
        if name == "python_unittest":
            from .python_unittest_runner import validate_manifest

            try:
                validate_manifest(parameters["manifest"], parameters["test_module"])
            except ValueError as exc:
                raise DomainError(str(exc)) from None
        return spec, encoded

    def _scope(self, name, parameters):
        if name == "gpu_status":
            return "gpu:local"
        if "path" in parameters:
            scope = GovernanceService(self.store)._workspace_scope(parameters["path"])
            core = Path(__file__).resolve().parent.parent
            target = Path(scope)
            protected = (core, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
            if name in {"file_write", "file_revise", "directory_create"} and any(
                target.is_relative_to(root) or root.is_relative_to(target) for root in protected
            ):
                raise DomainError("Core installation is protected")
            if any(
                part.lower() in {"secrets", "secret", "credentials", ".aws", ".azure", ".config"}
                or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
                for part in target.parts
            ):
                raise DomainError("Sensitive resource is protected")
            return scope
        if name in {"shell_inspect", "python_compile", "numeric_ast"}:
            return "process:diagnostic"
        if name == "browser_get":
            self._allowed_url(parameters["url"])
            return parameters["url"]
        if name == "mcp_call":
            config = self._mcp_servers.get(parameters["server"])
            if not config or parameters["tool"] not in config.get("tools", ()):
                raise DomainError("MCP tool is not allowlisted")
            self._allowed_url(config["url"])
            return f"mcp:{parameters['server']}:{parameters['tool']}"
        raise DomainError("Unsupported resource")

    def _context(
        self, work, quest_id, worker_id, run_id, lease_token, tool_name, call_id, parameters
    ):
        row = work.connection.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
        if (
            row is None
            or row["quest_id"] != quest_id
            or row["worker_id"] != worker_id
            or row["state"] != "running"
            or not lease_token
            or row["lease_token"] != lease_token
            or row["lease_until"] is None
            or row["lease_until"] <= time.time()
            or row["deadline"] <= time.time()
        ):
            raise DomainError("Worker execution lease unavailable")
        quest = work.get_quest(quest_id)
        plan = work.connection.execute(
            "SELECT * FROM quest_plans WHERE id=? AND quest_id=?", (row["plan_id"], quest_id)
        ).fetchone()
        node = work.connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
            (row["plan_id"], row["node_key"]),
        ).fetchone()
        if (
            quest is None
            or quest["status"] != "running"
            or plan is None
            or plan["status"] != "approved"
            or plan["revision"] != quest["plan_revision"]
            or plan["revision"] != row["plan_revision"]
            or plan["digest"] != row["plan_digest"]
            or node is None
            or node["worker_id"] != worker_id
            or node["status"] != "running"
        ):
            raise DomainError("Worker is outside the approved plan")
        try:
            tools = json.loads(row["allowed_tools_json"])
            plan_data = json.loads(plan["graph_json"])
            node_tools = json.loads(node["allowed_tools"])
        except (TypeError, ValueError) as exc:
            raise DomainError("Invalid approved execution scope") from exc
        if (
            not isinstance(tools, list)
            or not isinstance(node_tools, list)
            or any(not isinstance(item, str) for item in tools + node_tools)
            or tool_name not in tools
            or not set(tools).issubset(set(node_tools))
        ):
            raise DomainError("Tool is outside the worker scope")
        from .team_composition import TeamCompositionService

        team = TeamCompositionService(self.store).get_approved(
            quest_id, row["plan_revision"], row["team_digest"], connection=work.connection
        )
        try:
            member = team["members"][row["member_index"]]
            if (
                member["role_id"] != row["role_id"]
                or member["task_node_key"] != row["node_key"]
                or member["provider_id"] != row["provider_id"]
                or member["model_id"] != row["model_id"]
                or set(member["tool_scope"]) != set(tools)
            ):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DomainError("Worker differs from the approved team") from exc
        call = work.connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND call_id=?", (run_id, call_id)
        ).fetchone()
        if call is None:
            from .worker_prefetch import core_tool_reservation

            call = core_tool_reservation(
                self.store, work.connection, row, call_id, tool_name, parameters
            )
        if (
            call is None
            or call["tool_name"] != tool_name
            or call["state"] not in {"pending", "running"}
        ):
            raise DomainError("Tool call was not reserved by the Worker")
        try:
            reserved_parameters = json.loads(call["arguments_json"])
        except (ValueError, TypeError) as exc:
            raise DomainError("Invalid reserved tool parameters") from exc
        if parameter_digest(reserved_parameters) != parameter_digest(parameters):
            raise DomainError("Tool parameters differ from Worker reservation")
        if plan_data.get("max_cost_microunits") is not None and (
            not plan["cost_currency"] or row["currency"] != plan["cost_currency"]
        ):
            raise DomainError("Cost limit has unknown or mismatched currency")
        return row, quest, plan

    def prepare(
        self,
        *,
        call_id,
        quest_id,
        worker_id,
        run_id,
        tool_name,
        parameters,
        approval_id=None,
        lease_token=None,
    ):
        if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
            raise DomainError("Invalid tool call ID")
        spec, encoded = self._parameters(tool_name, parameters)
        scope = self._scope(tool_name, parameters)
        digest = parameter_digest(parameters)
        with self.store.unit_of_work() as work:
            run, quest, plan = self._context(
                work, quest_id, worker_id, run_id, lease_token, tool_name, call_id, parameters
            )
            existing = work.connection.execute(
                "SELECT * FROM action_intents WHERE call_id=?", (call_id,)
            ).fetchone()
            if existing:
                if (
                    existing["quest_id"],
                    existing["worker_id"],
                    existing["run_id"],
                    existing["tool_name"],
                    existing["parameter_digest"],
                    existing["resource_scope"],
                ) != (quest_id, worker_id, run_id, tool_name, digest, scope) or (
                    approval_id is not None and approval_id != existing["approval_id"]
                ):
                    raise DomainError("Tool call ID is already bound")
                return dict(existing)
            if tool_name == "python_unittest":
                self._python_test_snapshot(quest_id, scope, parameters)
            if tool_name == "file_revise":
                self._revision_source(work.connection, run_id, scope, parameters)
            if spec.risk == "R1":
                grants = work.connection.execute(
                    "SELECT resource_scope FROM task_grants WHERE quest_id=? AND workspace_id=? AND plan_revision=? AND revoked_at IS NULL",
                    (quest_id, self.store.workspace.id, quest["plan_revision"]),
                ).fetchall()
                if not any(
                    Path(scope).is_relative_to(Path(grant["resource_scope"])) for grant in grants
                ):
                    raise ToolAuthorizationRequired(tool_name, parameters, scope, "R1")
            if spec.risk == "R2":
                if approval_id is None:
                    matches = work.connection.execute(
                        "SELECT id FROM approvals WHERE quest_id=? AND plan_revision=? AND action_type=? AND parameter_digest=? AND resource_scope=? AND risk='R2' AND policy_version=? AND status='APPROVED' AND consumed_at IS NULL AND NOT EXISTS (SELECT 1 FROM tool_approval_requests b WHERE b.approval_id=approvals.id AND b.call_id<>?)",
                        (
                            quest_id,
                            quest["plan_revision"],
                            tool_name,
                            digest,
                            scope,
                            POLICY_VERSION,
                            call_id,
                        ),
                    ).fetchall()
                    if len(matches) == 1:
                        approval_id = matches[0]["id"]
                approval = (
                    work.connection.execute(
                        "SELECT * FROM approvals WHERE id=?", (approval_id,)
                    ).fetchone()
                    if approval_id
                    else None
                )
                if (
                    approval is None
                    or approval["quest_id"] != quest_id
                    or approval["plan_revision"] != quest["plan_revision"]
                    or approval["action_type"] != tool_name
                    or approval["parameter_digest"] != digest
                    or approval["resource_scope"] != scope
                    or approval["risk"] != "R2"
                    or approval["policy_version"] != POLICY_VERSION
                    or approval["status"] != "APPROVED"
                    or approval["consumed_at"] is not None
                    or work.connection.execute(
                        "SELECT 1 FROM tool_approval_requests WHERE approval_id=? AND call_id<>?",
                        (approval_id, call_id),
                    ).fetchone()
                    or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
                ):
                    raise ToolAuthorizationRequired(tool_name, parameters, scope, "R2")
                changed = work.connection.execute(
                    "UPDATE approvals SET consumed_at=? WHERE id=? AND status='APPROVED' AND consumed_at IS NULL",
                    (now_iso(), approval_id),
                )
                if changed.rowcount != 1:
                    raise DomainError("Approval already consumed")
            intent_id = new_id("action")
            work.connection.execute(
                "INSERT INTO action_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent_id,
                    call_id,
                    quest_id,
                    worker_id,
                    run_id,
                    plan["id"],
                    quest["plan_revision"],
                    tool_name,
                    encoded,
                    digest,
                    scope,
                    spec.risk,
                    approval_id,
                    run["execution_mode"],
                    "prepared",
                    now_iso(),
                    now_iso(),
                ),
            )
            work.append_event(
                ForgeEvent(
                    event_type="tool.intent_prepared",
                    quest_id=quest_id,
                    worker_id=worker_id,
                    payload={"intent_id": intent_id, "tool": tool_name},
                )
            )
            return dict(
                work.connection.execute(
                    "SELECT * FROM action_intents WHERE id=?", (intent_id,)
                ).fetchone()
            )

    def _begin(self, intent_id, lease_token):
        with self.store.unit_of_work() as work:
            intent = work.connection.execute(
                "SELECT * FROM action_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if intent is None:
                raise DomainError("Unknown action intent")
            parameters = json.loads(intent["parameters_json"])
            self._context(
                work,
                intent["quest_id"],
                intent["worker_id"],
                intent["run_id"],
                lease_token,
                intent["tool_name"],
                intent["call_id"],
                parameters,
            )
            if (
                parameter_digest(parameters) != intent["parameter_digest"]
                or self._scope(intent["tool_name"], parameters) != intent["resource_scope"]
            ):
                raise DomainError("Action binding changed")
            if intent["risk"] == "R1":
                grants = work.connection.execute(
                    "SELECT resource_scope FROM task_grants WHERE quest_id=? AND workspace_id=? AND plan_revision=? AND revoked_at IS NULL",
                    (intent["quest_id"], self.store.workspace.id, intent["plan_revision"]),
                ).fetchall()
                if not any(
                    Path(intent["resource_scope"]).is_relative_to(Path(grant["resource_scope"]))
                    for grant in grants
                ):
                    raise DomainError("Workspace grant was revoked")
            if intent["risk"] == "R2":
                approval = work.connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (intent["approval_id"],)
                ).fetchone()
                if (
                    approval is None
                    or approval["status"] != "APPROVED"
                    or not approval["consumed_at"]
                    or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
                ):
                    raise DomainError("Approval is no longer valid")
            if intent["state"] != "prepared":
                return dict(intent), False
            if intent["tool_name"] == "file_revise":
                self._revision_source(
                    work.connection,
                    intent["run_id"],
                    intent["resource_scope"],
                    parameters,
                    intent_id=intent_id,
                )
            changed = work.connection.execute(
                "UPDATE action_intents SET state='executing',updated_at=? WHERE id=? AND state='prepared'",
                (now_iso(), intent_id),
            )
            return dict(intent), changed.rowcount == 1

    def _allowed_url(self, url):
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            if (
                parsed.scheme != "https"
                or not host
                or host not in self._network_hosts
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.port not in (None, 443)
            ):
                raise ValueError
        except (ValueError, OSError) as exc:
            raise DomainError("Network target is not allowed") from exc
        return parsed

    @staticmethod
    def _read_revision_target(path):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise DomainError("Artifact revision target is not a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DomainError("Artifact revision target is not a private regular file")
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise DomainError("Artifact revision target exceeds size limit")
        return data, (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)

    def _revision_source(self, connection, run_id, scope, parameters, *, intent_id=None):
        source = verify_file_revision_chain(connection, run_id, parameters["prior_receipt_id"])
        latest = connection.execute(
            "SELECT r.id FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
            "WHERE i.run_id=? AND i.resource_scope=? AND i.tool_name IN ('file_write','file_revise') "
            "AND i.state='succeeded' ORDER BY r.rowid DESC LIMIT 1",
            (run_id, scope),
        ).fetchone()
        unsettled = connection.execute(
            "SELECT 1 FROM action_intents WHERE run_id=? AND resource_scope=? "
            "AND state IN ('prepared','executing','uncertain') AND id<>? LIMIT 1",
            (run_id, scope, intent_id or ""),
        ).fetchone()
        if (
            source["path"] != scope
            or source["sha256"] != parameters["expected_sha256"]
            or latest is None
            or latest[0] != source["receipt_id"]
            or unsettled is not None
        ):
            raise DomainError("Artifact revision source is stale or unresolved")
        data, identity = self._read_revision_target(Path(scope))
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise DomainError("Artifact revision content changed outside its receipt chain")
        return source, identity

    def _perform_revision(self, intent, parameters, lease_token, cancelled):
        path = Path(intent["resource_scope"])
        data = parameters["content"].encode("utf-8")
        if len(data) > _MAX_BYTES:
            raise DomainError("File exceeds write limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".forge-revise-", dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Core mutation/authorization and the final expected-content check share
            # one write lock. Every earlier version is already retained in immutable
            # intent parameters and the prior receipt chain before replacement.
            with self.store.unit_of_work() as work:
                self._context(
                    work,
                    intent["quest_id"],
                    intent["worker_id"],
                    intent["run_id"],
                    lease_token,
                    "file_revise",
                    intent["call_id"],
                    parameters,
                )
                if self._scope("file_revise", parameters) != str(path):
                    raise DomainError("Artifact revision target changed")
                grants = work.connection.execute(
                    "SELECT resource_scope FROM task_grants WHERE quest_id=? AND workspace_id=? "
                    "AND plan_revision=? AND revoked_at IS NULL",
                    (intent["quest_id"], self.store.workspace.id, intent["plan_revision"]),
                ).fetchall()
                if not any(path.is_relative_to(Path(grant[0])) for grant in grants):
                    raise DomainError("Workspace grant was revoked")
                source, identity = self._revision_source(
                    work.connection,
                    intent["run_id"],
                    str(path),
                    parameters,
                    intent_id=intent["id"],
                )
                current_data, current_identity = self._read_revision_target(path)
                if (
                    cancelled.is_set()
                    or current_identity != identity
                    or hashlib.sha256(current_data).hexdigest() != source["sha256"]
                ):
                    raise DomainError("Artifact revision target or authorization changed")
                os.replace(temporary, path)
            return {
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "prior_receipt_id": source["receipt_id"],
                "prior_sha256": source["sha256"],
            }
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def _public_url(self, url, cancelled, deadline=None):
        parsed = self._allowed_url(url)
        resolved = []
        done = threading.Event()

        def resolve():
            try:
                resolved.extend(socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM))
            except OSError:
                pass
            finally:
                done.set()

        threading.Thread(target=resolve, daemon=True).start()
        resolution_deadline = min(time.monotonic() + 5, deadline or float("inf"))
        while not done.is_set():
            remaining = resolution_deadline - time.monotonic()
            if remaining <= 0 or cancelled.is_set():
                raise DomainError("Network resolution timed out or was cancelled")
            done.wait(min(remaining, 0.05))
        try:
            addresses = {item[4][0] for item in resolved}
            if not addresses or any(
                not ipaddress.ip_address(address).is_global for address in addresses
            ):
                raise ValueError
        except (ValueError, OSError) as exc:
            raise DomainError("Network target is not allowed") from exc
        return parsed, min(addresses)

    def _network_request(self, url, method, payload, cancelled):
        deadline = time.monotonic() + 10
        parsed, address = self._public_url(url, cancelled, deadline)
        if cancelled.is_set():
            raise DomainError("Action was cancelled")

        class PinnedHTTPSConnection(http.client.HTTPSConnection):
            def connect(self):
                remaining = deadline - time.monotonic()
                if cancelled.is_set() or remaining <= 0:
                    raise DomainError("Network request timed out or was cancelled")
                # Register before connect so the deadline watchdog can interrupt
                # connect as well as TLS/header/body reads.
                raw = socket.socket(
                    socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM
                )
                self._pinned_socket = raw
                self.sock = raw
                try:
                    raw.settimeout(remaining)
                    raw.connect((address, 443))
                    if timed_out.is_set() or cancelled.is_set():
                        raise DomainError("Network request timed out or was cancelled")
                    raw.settimeout(max(0.001, deadline - time.monotonic()))
                    self.sock = ssl.create_default_context().wrap_socket(
                        raw, server_hostname=parsed.hostname
                    )
                    self._pinned_socket = self.sock
                    if timed_out.is_set() or cancelled.is_set():
                        raise DomainError("Network request timed out or was cancelled")
                except BaseException:
                    raw.close()
                    raise

        connection = PinnedHTTPSConnection(parsed.hostname, port=443, timeout=10)
        timed_out = threading.Event()

        def abort():
            timed_out.set()
            active_socket = getattr(connection, "_pinned_socket", None)
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

        timer = threading.Timer(max(0.001, deadline - time.monotonic()), abort)
        timer.daemon = True
        timer.start()
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            encoded = _json(payload).encode("utf-8") if payload is not None else None
            headers = {"Accept": "application/json, text/plain"}
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            body = response.read(_MAX_BYTES + 1)
            if timed_out.is_set() or cancelled.is_set():
                raise DomainError("Network request timed out or was cancelled")
            if len(body) > _MAX_BYTES:
                raise DomainError("Response exceeds limit")
            return {
                "status_code": response.status,
                "body": body.decode("utf-8", "replace"),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        finally:
            timer.cancel()
            connection.close()

    @staticmethod
    def _environment():
        return {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }

    @staticmethod
    def _validate_output(spec, result):
        schema = spec.output_schema
        if not isinstance(result, dict) or set(result) != set(schema["required"]):
            raise DomainError("Tool output violates its contract")
        for key, rule in schema["properties"].items():
            value = result[key]
            if rule["type"] == "string" and (
                not isinstance(value, str) or len(value) > rule["maxLength"]
            ):
                raise DomainError("Tool output violates its contract")
            if rule["type"] == "integer" and type(value) is not int:
                raise DomainError("Tool output violates its contract")
            if rule["type"] == "array" and (
                not isinstance(value, list)
                or len(value) > rule["maxItems"]
                or any(
                    not isinstance(item, str) or len(item) > rule["items"]["maxLength"]
                    for item in value
                )
            ):
                raise DomainError("Tool output violates its contract")

    @staticmethod
    def _run_process(argv, cwd, env, data=None):
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        buffers = [bytearray(), bytearray()]
        exceeded = threading.Event()
        timed_out = threading.Event()

        def abort():
            timed_out.set()
            try:
                process.kill()
            except OSError:
                pass

        timer = threading.Timer(10, abort)
        timer.daemon = True
        timer.start()

        def read_pipe(pipe, output):
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    return
                output.extend(chunk)
                if sum(map(len, buffers)) > _MAX_BYTES:
                    exceeded.set()
                    process.kill()
                    return

        threads = [
            threading.Thread(target=read_pipe, args=(pipe, output), daemon=True)
            for pipe, output in zip((process.stdout, process.stderr), buffers)
        ]
        for thread in threads:
            thread.start()
        try:
            if data is not None:
                process.stdin.write(data)
                process.stdin.close()
            process.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            process.kill()
            process.wait()
            raise DomainError("Process failed or timed out") from None
        finally:
            timer.cancel()
            for thread in threads:
                thread.join(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        if timed_out.is_set() or exceeded.is_set() or any(thread.is_alive() for thread in threads):
            raise DomainError("Process output exceeds limit")
        stdout, stderr = map(bytes, buffers)
        return {
            "argv": argv,
            "cwd": cwd,
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }

    def _git_repository(self, path):
        metadata = path / ".git"
        if (
            path.is_symlink()
            or metadata.is_symlink()
            or not metadata.is_dir()
            or (metadata / "commondir").exists()
            or (metadata / "objects" / "info" / "alternates").exists()
        ):
            raise DomainError("Only ordinary workspace Git repositories are supported")
        config = metadata / "config"
        if config.is_symlink() or not config.is_file() or config.stat().st_size > 65536:
            raise DomainError("Unsafe Git configuration")
        text = config.read_text(encoding="utf-8")
        section = None
        allowed = {
            "repositoryformatversion",
            "filemode",
            "bare",
            "logallrefupdates",
            "ignorecase",
            "precomposeunicode",
            "symlinks",
        }
        for line in text.splitlines():
            value = line.strip()
            if not value or value.startswith(("#", ";")):
                continue
            if value.startswith("["):
                if value.lower() != "[core]":
                    raise DomainError("Only minimal Git configuration is supported")
                section = "core"
            elif (
                section != "core"
                or not re.fullmatch(r"[A-Za-z]+\s*=\s*[A-Za-z0-9]+", value)
                or value.split("=", 1)[0].strip().lower() not in allowed
            ):
                raise DomainError("Unsafe Git configuration")
        count = 0
        for directory, names, files in os.walk(metadata, followlinks=False):
            for name in names + files:
                count += 1
                item = Path(directory) / name
                if (
                    count > 100000
                    or item.is_symlink()
                    or (hasattr(item, "is_junction") and item.is_junction())
                ):
                    raise DomainError("Unsafe Git metadata")
        return metadata

    def _python_test_snapshot(self, quest_id, scope, parameters):
        from .python_unittest_runner import read_snapshot

        root = Path(self.store.workspace.root).resolve() / "work" / quest_id
        if not Path(scope).is_relative_to(root):
            raise DomainError("Python execution is confined to this task's artifact directory")
        try:
            return read_snapshot(scope, parameters["manifest"], parameters["test_module"])
        except (OSError, ValueError, UnicodeError) as exc:
            raise DomainError("Python test snapshot is unavailable or differs from approval") from exc

    def _directory_create_path(self, parameters, scope, *, allow_missing):
        """Accept only the original, unlinked directory path and ordinary parents."""
        workspace = Path(self.store.workspace.root).resolve()
        supplied = Path(parameters["path"])
        if not supplied.is_absolute():
            supplied = workspace / supplied
        path = Path(scope)
        if Path(os.path.abspath(supplied)) != path:
            raise DomainError("Directory path must not be linked")
        current = workspace
        for part in path.relative_to(workspace).parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if allow_missing and current == path:
                    continue
                raise DomainError("Directory path is unavailable") from None
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or current.resolve() != current
            ):
                raise DomainError("Directory path must be ordinary and unlinked")

    def _perform(self, intent, lease_token, cancelled):
        checked, _ = self._begin(intent["id"], lease_token)
        if checked["state"] != "executing" or cancelled.is_set():
            raise DomainError("Action is no longer authorized")
        parameters = json.loads(intent["parameters_json"])
        name = intent["tool_name"]
        if "path" in parameters and self._scope(name, parameters) != intent["resource_scope"]:
            raise DomainError("Resource changed before execution")
        path = Path(intent["resource_scope"]) if "path" in parameters else None
        if name == "python_unittest":
            from .python_unittest_runner import run_snapshot
            from .worker_environment import process_environment, worker_environment

            snapshot = self._python_test_snapshot(intent["quest_id"], str(path), parameters)
            environment = worker_environment(self.store, intent["run_id"])
            private = Path(environment["cwd"]) / ("unittest-" + intent["id"])
            result = run_snapshot(
                snapshot,
                parameters["test_module"],
                cwd=private,
                env=process_environment(self._environment(), environment),
                cancelled=cancelled,
            )
            return {**result, "path": str(path)}
        if name == "file_read":
            with path.open("rb") as stream:
                data = stream.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise DomainError("File exceeds read limit")
            from .worker_envelope import verify_read_identity

            with self.store.connection() as connection:
                connection.execute("BEGIN")
                run = connection.execute(
                    "SELECT * FROM worker_runs WHERE id=?", (intent["run_id"],)
                ).fetchone()
                verify_read_identity(self.store, connection, run, path, data)
            return {"content": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()}
        if name == "file_write":
            data = parameters["content"].encode("utf-8")
            if len(data) > _MAX_BYTES:
                raise DomainError("File exceeds write limit")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return {
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        if name == "file_revise":
            return self._perform_revision(intent, parameters, lease_token, cancelled)
        if name == "directory_list":
            entries = []
            with os.scandir(path) as iterator:
                for item in iterator:
                    entries.append(item.name)
                    if len(entries) > 1000:
                        raise DomainError("Directory exceeds listing limit")
            entries.sort()
            return {"entries": entries}
        if name == "directory_create":
            self._directory_create_path(parameters, str(path), allow_missing=True)
            try:
                path.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                # A precreated task root is already a valid result. This does not
                # settle or replay any prior unknown intent: execute's existing
                # durable state fence runs before reaching this operation.
                pass
            self._directory_create_path(parameters, str(path), allow_missing=False)
            return {"path": str(path)}
        if name == "gpu_status":
            from .gpu_tools import probe_gpu
            from .worker_environment import process_environment, worker_environment

            environment = worker_environment(self.store, intent["run_id"])
            return probe_gpu(cwd=environment["cwd"],
                env=process_environment(self._environment(), environment), runner=self._run_process)
        if name in {"shell_inspect", "git_status", "python_compile", "numeric_ast", "python_test"}:
            from .worker_environment import process_environment, worker_environment

            environment = worker_environment(self.store, intent["run_id"])
            data = None
            if name == "shell_inspect":
                argv = [sys.executable, "-I", "--version"]
                cwd = environment["cwd"]
            elif name == "python_compile":
                argv = [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys; compile(sys.stdin.read(), '<candidate>', 'exec')",
                ]
                cwd = environment["cwd"]
                data = parameters["source"].encode("utf-8")
            elif name == "numeric_ast":
                argv = [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("numeric_harness.py").resolve()),
                ]
                cwd = environment["cwd"]
                data = _json(
                    {"source": parameters["source"], "inputs": parameters["inputs"]}
                ).encode("utf-8")
                if len(data) > 65536:
                    raise DomainError("Numeric input exceeds limit")
            elif name == "python_test":
                with path.open("rb") as stream:
                    candidate = stream.read(16001)
                if len(candidate) > 16000:
                    raise DomainError("Test source exceeds limit")
                argv = [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("code_test_harness.py").resolve()),
                ]
                cwd = environment["cwd"]
                data = _json(
                    {
                        "source": candidate.decode("utf-8"),
                        "function": parameters["function"],
                        "suite": parameters["suite"],
                    }
                ).encode("utf-8")
                if len(data) > 65536:
                    raise DomainError("Test input exceeds limit")
            else:
                binary = shutil.which("git")
                if binary is None:
                    raise DomainError("Git is unavailable")
                git_dir = self._git_repository(path)
                argv = [
                    binary,
                    "--no-optional-locks",
                    f"--git-dir={git_dir}",
                    f"--work-tree={path}",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    "-c",
                    "core.pager=cat",
                    "-c",
                    "diff.external=",
                    "status",
                    "--short",
                    "--untracked-files=no",
                ]
                cwd = environment["cwd"]
            env = process_environment(self._environment(), environment)
            env.update(
                {
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_PAGER": "cat",
                    "GIT_OPTIONAL_LOCKS": "0",
                }
            )
            result = self._run_process(argv, cwd, env, data)
            if name == "python_compile":
                result["source_sha256"] = hashlib.sha256(data).hexdigest()
            elif name == "numeric_ast":
                output = json.loads(result["stdout"]) if result["exit_code"] == 0 else None
                result.update(
                    result_json=_json(output["result"]) if output else "",
                    steps=output["steps"] if output else 0,
                    subset=output["subset"] if output else "forge_numeric_ast_v1",
                    source_sha256=hashlib.sha256(parameters["source"].encode("utf-8")).hexdigest(),
                    input_sha256=hashlib.sha256(
                        _json(parameters["inputs"]).encode("utf-8")
                    ).hexdigest(),
                )
            elif name == "python_test":
                output = json.loads(result["stdout"]) if result["exit_code"] in {0, 2} else None
                result.update(
                    path=str(path),
                    source_sha256=hashlib.sha256(candidate).hexdigest(),
                    suite_sha256=hashlib.sha256(_json(parameters["suite"]).encode()).hexdigest(),
                    passed=output["passed"] if output else 0,
                    failed=output["failed"] if output else 0,
                    result_json=_json(output) if output else "",
                    subset="forge_pure_function_tests_v1",
                )
            return result
        if name in {"browser_get", "mcp_call"}:
            if name == "browser_get":
                url = parameters["url"]
                method, payload = "GET", None
            else:
                config = self._mcp_servers.get(parameters["server"])
                if not config or parameters["tool"] not in config.get("tools", ()):
                    raise DomainError("MCP tool is not allowlisted")
                url = config["url"]
                method, payload = (
                    "POST",
                    {
                        "jsonrpc": "2.0",
                        "id": intent["call_id"],
                        "method": "tools/call",
                        "params": {
                            "name": parameters["tool"],
                            "arguments": parameters["arguments"],
                        },
                    },
                )
            return self._network_request(url, method, payload, cancelled)
        raise DomainError("Tool is not implemented")

    def _finish(self, intent_id, state, result, lease_token):
        encoded = _json(result)
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM action_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if row is None or row["state"] != "executing":
                raise DomainError("Action is no longer executing")
            try:
                self._context(
                    work,
                    row["quest_id"],
                    row["worker_id"],
                    row["run_id"],
                    lease_token,
                    row["tool_name"],
                    row["call_id"],
                    json.loads(row["parameters_json"]),
                )
            except DomainError:
                state = "uncertain"
            receipt_id = new_id("receipt")
            work.connection.execute(
                "INSERT INTO action_receipts VALUES (?,?,?,?,?,?)",
                (
                    receipt_id,
                    intent_id,
                    row["execution_mode"],
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    now_iso(),
                ),
            )
            work.connection.execute(
                "UPDATE action_intents SET state=?,updated_at=? WHERE id=? AND state='executing'",
                (state, now_iso(), intent_id),
            )
            evidence_payload = {
                "receipt_id": receipt_id,
                "intent_id": intent_id,
                "run_id": row["run_id"],
                "plan_revision": row["plan_revision"],
                "state": state,
                "execution_mode": row["execution_mode"],
                "result_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            }
            work.connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (
                    new_id("evidence"),
                    row["quest_id"],
                    "tool",
                    int(row["execution_mode"] == "live" and state != "uncertain"),
                    _json(evidence_payload),
                    now_iso(),
                ),
            )
            work.append_event(
                ForgeEvent(
                    event_type="tool.receipt_recorded",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload={"intent_id": intent_id, "receipt_id": receipt_id, "state": state},
                )
            )
        return {
            "call_id": row["call_id"],
            "intent_id": intent_id,
            "receipt_id": receipt_id,
            "state": state,
            "output": result,
        }

    async def execute(
        self,
        *,
        call_id,
        quest_id,
        worker_id,
        run_id,
        lease_token,
        tool_name,
        parameters,
        approval_id=None,
    ):
        try:
            intent = self.prepare(
                call_id=call_id,
                quest_id=quest_id,
                worker_id=worker_id,
                run_id=run_id,
                lease_token=lease_token,
                tool_name=tool_name,
                parameters=parameters,
                approval_id=approval_id,
            )
        except ToolAuthorizationRequired as exc:
            if exc.details["risk"] == "R2":
                with self.store.unit_of_work() as work:
                    _run, quest, _ = self._context(
                        work,
                        quest_id,
                        worker_id,
                        run_id,
                        lease_token,
                        tool_name,
                        call_id,
                        parameters,
                    )
                    pending = work.connection.execute(
                        "SELECT a.id FROM approvals a JOIN tool_approval_requests b ON b.approval_id=a.id WHERE b.call_id=? AND b.run_id=? AND a.quest_id=? AND a.plan_revision=? AND a.action_type=? AND a.parameter_digest=? AND a.resource_scope=? AND a.risk='R2' AND a.policy_version=? AND a.status IN ('PENDING','APPROVED') AND a.consumed_at IS NULL AND a.expires_at>?",
                        (
                            call_id,
                            run_id,
                            quest_id,
                            quest["plan_revision"],
                            tool_name,
                            exc.details["parameter_digest"],
                            exc.details["resource_scope"],
                            POLICY_VERSION,
                            now_iso(),
                        ),
                    ).fetchone()
                    if pending is None:
                        approval = GovernanceService(self.store).request_approval(
                            quest_id,
                            tool_name,
                            parameters,
                            exc.details["resource_scope"],
                            "R2",
                            exc.details["reason"],
                            _work=work,
                        )
                        work.connection.execute(
                            "INSERT INTO tool_approval_requests VALUES (?,?,?) ON CONFLICT(call_id) DO UPDATE SET approval_id=excluded.approval_id WHERE tool_approval_requests.run_id=excluded.run_id",
                            (call_id, run_id, approval["id"]),
                        )
            raise
        intent, execute = self._begin(intent["id"], lease_token)
        if not execute:
            with self.store.connection() as connection:
                receipt = connection.execute(
                    "SELECT * FROM action_receipts WHERE intent_id=?", (intent["id"],)
                ).fetchone()
            if receipt:
                return {
                    "call_id": call_id,
                    "intent_id": intent["id"],
                    "receipt_id": receipt["id"],
                    "state": intent["state"],
                    "output": json.loads(receipt["result_json"]),
                }
            return {
                "call_id": call_id,
                "intent_id": intent["id"],
                "receipt_id": None,
                "state": "uncertain" if intent["state"] == "executing" else intent["state"],
                "output": None,
            }
        cancelled = threading.Event()
        try:
            result = await asyncio.to_thread(self._perform, intent, lease_token, cancelled)
            self._validate_output(self._specs[tool_name], result)
            failed = "exit_code" in result and result["exit_code"] != 0
            if "status_code" in result and not 200 <= result["status_code"] < 300:
                failed = True
            if tool_name == "mcp_call" and not failed:
                try:
                    response = json.loads(result["body"])
                    if (
                        not isinstance(response, dict)
                        or response.get("jsonrpc") != "2.0"
                        or response.get("id") != call_id
                        or "error" in response
                        or "result" not in response
                    ):
                        failed = True
                except (TypeError, ValueError):
                    failed = True
            state = "failed" if failed else "succeeded"
            return self._finish(intent["id"], state, result, lease_token)
        except asyncio.CancelledError:
            cancelled.set()
            self._mark_uncertain(intent["id"])
            raise
        except Exception as exc:  # noqa: BLE001 - side effects may have occurred before failure
            cancelled.set()
            if tool_name in {"file_read", "directory_list"} and isinstance(
                exc, (OSError, UnicodeError, DomainError)
            ):
                # These fixed local reads cannot mutate an external resource.
                # Record a real failure receipt so a Worker can correct a path;
                # _finish still downgrades stale authorization to uncertain.
                code = (
                    "resource_unavailable"
                    if isinstance(exc, FileNotFoundError)
                    else "resource_denied"
                    if isinstance(exc, PermissionError)
                    else "resource_not_utf8"
                    if isinstance(exc, UnicodeError)
                    else "read_policy_rejected"
                    if isinstance(exc, DomainError)
                    else "resource_read_failed"
                )
                with suppress(Exception):
                    return self._finish(intent["id"], "failed", {"error_code": code}, lease_token)
            self._mark_uncertain(intent["id"])
            return {
                "call_id": call_id,
                "intent_id": intent["id"],
                "receipt_id": None,
                "state": "uncertain",
                "output": None,
            }

    def _mark_uncertain(self, intent_id):
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM action_intents WHERE id=? AND state='executing'", (intent_id,)
            ).fetchone()
            if row:
                work.connection.execute(
                    "UPDATE action_intents SET state='uncertain',updated_at=? WHERE id=? AND state='executing'",
                    (now_iso(), intent_id),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="tool.execution_uncertain",
                        quest_id=row["quest_id"],
                        worker_id=row["worker_id"],
                        payload={"intent_id": intent_id},
                    )
                )

    def recover_abandoned(self):
        recovered = 0
        with self.store.unit_of_work() as work:
            rows = work.connection.execute(
                "SELECT a.id FROM action_intents a LEFT JOIN worker_runs r ON r.id=a.run_id WHERE a.state='executing' AND (r.id IS NULL OR r.state!='running' OR r.lease_until IS NULL OR r.lease_until<=?)",
                (time.time(),),
            ).fetchall()
            for row in rows:
                intent = work.connection.execute(
                    "SELECT * FROM action_intents WHERE id=? AND state='executing'", (row["id"],)
                ).fetchone()
                if intent is None:
                    continue
                work.connection.execute(
                    "UPDATE action_intents SET state='uncertain',updated_at=? WHERE id=? AND state='executing'",
                    (now_iso(), row["id"]),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="tool.execution_uncertain",
                        quest_id=intent["quest_id"],
                        worker_id=intent["worker_id"],
                        payload={"intent_id": intent["id"]},
                    )
                )
                recovered += 1
        return recovered
