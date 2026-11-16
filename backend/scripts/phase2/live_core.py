"""Phase 2 live Core harness: spawn an isolated Core, authenticate, drive the API.

Credential handling: the provider key is read from a process environment variable
named by FORGE_PROVIDER_CREDENTIAL_REF. Nothing secret is written to disk or logs.
"""

import hashlib
import hmac
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"

PROVIDER_ID = "wb-local"
MODEL_ID = "deepseek-v4.1-flash"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveCore:
    def __init__(
        self,
        workspace,
        *,
        provider_env,
        token_limit,
        concurrency,
        log=None,
        extra_env=None,
        private_control=False,
    ):
        if token_limit is not None and (type(token_limit) is not int or token_limit < 1):
            raise ValueError("token_limit must be a positive integer or None")
        if type(private_control) is not bool:
            raise ValueError("private_control must be a boolean")
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.secret = secrets.token_hex(32)
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.session = None
        self.bearer = None
        self.process = None
        self.provider_env = provider_env
        self.token_limit = token_limit
        self.concurrency = concurrency
        self.log_path = log or self.workspace / "core.log"
        self.extra_env = extra_env or {}
        self.stdout = None
        self.private_control = private_control

    def env(self):
        raw = bytes.fromhex(self.secret)
        assert len(raw) == 32
        values = {
            "FORGE_WORKSPACE_ROOT": str(self.workspace),
            "FORGE_DB_PATH": "data/forge.sqlite3",
            "FORGE_HOST": "127.0.0.1",
            "FORGE_PORT": str(self.port),
            "FORGE_CORE_SECRET": self.secret,
            "FORGE_ROUTER_MODEL_ID": MODEL_ID,
            "FORGE_ROUTER_CONTEXT_LIMIT": "65536",
            "FORGE_ROUTER_OUTPUT_RESERVE": "4096",
            "FORGE_WORKER_LEASE_SECONDS": "150",
            "FORGE_REVIEW_MAX_ROUNDS": "3",
            "FORGE_ENFORCE_TOKEN_BUDGETS": "false" if self.token_limit is None else "true",
            "FORGE_MODEL_CONCURRENCY_LIMIT": str(self.concurrency),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if self.token_limit is not None:
            values["FORGE_MODEL_TOKEN_LIMIT"] = str(self.token_limit)
        values.update(self.provider_env)
        values.update(self.extra_env)
        expected_policy = "false" if self.token_limit is None else "true"
        if values.get("FORGE_ENFORCE_TOKEN_BUDGETS") != expected_policy:
            raise ValueError("Token budget policy conflicts with token_limit")
        if self.token_limit is None:
            if "FORGE_MODEL_TOKEN_LIMIT" in values:
                raise ValueError("Disabled token budgets must omit FORGE_MODEL_TOKEN_LIMIT")
        else:
            try:
                configured_limit = int(values["FORGE_MODEL_TOKEN_LIMIT"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid model token limit") from exc
            if configured_limit < 1 or configured_limit > self.token_limit:
                raise ValueError("Model token limit must stay within the harness limit")
        base = {k: v for k, v in os.environ.items() if not k.startswith("FORGE_")}
        base.update(values)
        return base

    def start(self, timeout=120):
        # Kept open for the child lifetime and closed by stop(), including failures.
        self.stdout = open(self.log_path, "ab")  # noqa: SIM115
        env = self.env()
        command = [str(PYTHON), "-m", "forge_api", "--workspace-root", str(self.workspace)]
        control_options = {}
        if self.private_control:
            command.append("--core-child")
            control_options["stdin"] = subprocess.PIPE
            env.pop("FORGE_CORE_SECRET", None)
        creation = 0
        if os.name == "nt":
            creation = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            command,
            cwd=str(BACKEND),
            env=env,
            stdout=self.stdout,
            stderr=subprocess.STDOUT,
            creationflags=creation,
            **control_options,
        )
        if self.private_control:
            from forge_api.desktop_host import write_frame

            write_frame(
                self.process.stdin, {"version": 1, "type": "bootstrap", "secret": self.secret}
            )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Core exited during startup")
            try:
                response = httpx.get(self.base_url + "/health", timeout=2, trust_env=False)
                if response.status_code == 200:
                    self.session = response.json()["session"]
                    raw = bytes.fromhex(self.secret)
                    self.bearer = hmac.new(raw, self.session.encode(), hashlib.sha256).hexdigest()
                    return self
            except (httpx.TransportError, httpx.TimeoutException):
                pass
            time.sleep(0.25)
        raise RuntimeError("Core did not become healthy")

    def client(self, timeout=240):
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": "Bearer " + self.bearer},
            timeout=httpx.Timeout(connect=5, read=timeout, write=10, pool=5),
            trust_env=False,
            follow_redirects=False,
        )

    def call(self, method, path, payload=None, timeout=240):
        with self.client(timeout=timeout) as client:
            response = client.request(method, path, json=payload)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:400]}
        return {"status": response.status_code, "body": body}

    def stop(self, *, graceful=True):
        """Confirm private graceful exit; forced cleanup never authorizes restart."""
        result = {
            "protocol": "private_core_control_v1"
            if self.private_control
            else "legacy_terminate_v1",
            "graceful_requested": graceful,
            "graceful_confirmed": False,
            "returncode": None,
            "forced_cleanup": False,
            "outcome": "not_started",
        }
        process = self.process
        try:
            if process is None:
                return result
            result["returncode"] = process.poll()
            if result["returncode"] is not None:
                result["outcome"] = "already_stopped_unconfirmed"
                return result
            private_shutdown = self.private_control and graceful
            if private_shutdown:
                from forge_api.desktop_host import write_frame

                try:
                    if process.stdin is None:
                        raise ValueError("Private control pipe missing")
                    write_frame(process.stdin, {"version": 1, "type": "shutdown"})
                except (OSError, ValueError):
                    result["outcome"] = "shutdown_control_failed_cleanup"
                    result["forced_cleanup"] = True
                    process.kill()
                    result["returncode"] = process.wait(timeout=15)
                    return result
            elif graceful:
                process.terminate()
            else:
                result["forced_cleanup"] = True
                process.kill()
            try:
                result["returncode"] = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                result["outcome"] = "shutdown_timeout_forced_cleanup"
                result["forced_cleanup"] = True
                process.kill()
                result["returncode"] = process.wait(timeout=15)
                return result
            if private_shutdown:
                result["graceful_confirmed"] = result["returncode"] == 0
                result["outcome"] = (
                    "private_shutdown_confirmed"
                    if result["graceful_confirmed"]
                    else "private_shutdown_exit_unconfirmed"
                )
            else:
                result["outcome"] = (
                    "legacy_terminated_unconfirmed" if graceful else "forced_cleanup"
                )
            return result
        finally:
            if self.private_control and process is not None and process.stdin is not None:
                process.stdin.close()
            if self.stdout:
                self.stdout.close()
                self.stdout = None

    def tail_log(self, limit=4000):
        if not self.log_path.exists():
            return ""
        data = self.log_path.read_bytes()[-limit:]
        return data.decode("utf-8", "replace")


def provider_env_from_key(key):
    return {
        "FORGE_PROVIDER_ID": PROVIDER_ID,
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "http://127.0.0.1:8788/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_LIVE_TEST",
        "FORGE_PROVIDER_SUPPORTS_TOOLS": "true",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA": "true",
        "FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK": "true",
        "FORGE_PROVIDER_TIMEOUT_SECONDS": "120",
        "FORGE_PROVIDER_MAX_RETRIES": "0",
        "FORGE_PROVIDER_REASONING_EFFORT": "none",
        "FORGE_PROVIDER_KEY_LIVE_TEST": key,
    }


MODEL_PROFILE = {
    "id": MODEL_ID,
    "provider": PROVIDER_ID,
    "display_name": "DeepSeek-V4.1-Flash (local wb-in)",
    "enabled": True,
    "model_id": MODEL_ID,
    "version": "wb-in",
    # Conservative operator ceilings, not experimentally verified model maxima.
    "context_window": 65536,
    "max_output_tokens": 8192,
    "tool_calling_support": {"supported": True, "source": "user_override"},
    "structured_output_support": {"supported": True, "source": "user_override"},
    "capability_measurements": {
        "coding": {"value": 70, "source": "exploratory_operator_prior", "n_samples": None},
        "mathematics": {"value": 65, "source": "exploratory_operator_prior", "n_samples": None},
        "architecture": {"value": 68, "source": "exploratory_operator_prior", "n_samples": None},
        "research": {"value": 62, "source": "exploratory_operator_prior", "n_samples": None},
        "planning": {"value": 66, "source": "exploratory_operator_prior", "n_samples": None},
        "writing": {"value": 68, "source": "exploratory_operator_prior", "n_samples": None},
        "design": {"value": 50, "source": "exploratory_operator_prior", "n_samples": None},
        "tool_calling": {"value": 70, "source": "exploratory_operator_prior", "n_samples": None},
        "instruction_following": {
            "value": 70,
            "source": "exploratory_operator_prior",
            "n_samples": None,
        },
    },
    "provenance": {
        "capability_note": "exploratory operator eligibility priors; zero benchmark samples; not measured capability",
        "reasoning_effort": "none; explicit operator choice, advertised by the local proxy implementation",
        "credential": "process environment only",
    },
    "custom_attributes": {
        "limits_source": "operator_ceiling_not_actual_maximum",
        "capability_source": "exploratory_prior_not_model_evaluation",
    },
}


def register_model(core):
    return core.call("POST", "/api/models", MODEL_PROFILE)
