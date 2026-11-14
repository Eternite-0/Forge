"""Process-local authentication and browser-origin boundary."""

import hashlib
import hmac
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
    "http://tauri.localhost",
)


class SessionGuard:
    def __init__(self):
        secret = os.environ.get("FORGE_CORE_SECRET", "")
        # A 256-bit random secret is passed by the trusted parent process only.
        try:
            raw = bytes.fromhex(secret)
        except ValueError as exc:
            raise ValueError("FORGE_CORE_SECRET must be 64 hex characters") from exc
        if len(raw) != 32 or len(secret) != 64 or len(set(raw)) < 12:
            raise ValueError("FORGE_CORE_SECRET must be a random 256-bit hex secret")
        self.session = secrets.token_hex(32)
        self._token = hmac.new(raw, self.session.encode(), hashlib.sha256).hexdigest()

    def authenticated(self, request):
        values = request.headers.getlist("authorization")
        if len(values) != 1 or len(values[0]) != 71 or not values[0].isascii():
            return False
        return hmac.compare_digest(values[0], f"Bearer {self._token}")

    async def __call__(self, request, call_next):
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id

        def reject(status, code, detail):
            return JSONResponse(
                status_code=status,
                content={"code": code, "detail": detail, "request_id": request_id},
            )

        host = request.headers.get("host", "")
        try:
            parsed = urlsplit("http://" + host)
            valid_host = (
                parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.username is None
                and parsed.password is None
                and parsed.path == ""
                and parsed.query == ""
                and parsed.fragment == ""
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                valid_host = False
        except ValueError:
            valid_host = False
        client = request.client.host if request.client else ""
        try:
            loopback = ipaddress.ip_address(client).is_loopback
        except ValueError:
            loopback = False
        origin = request.headers.get("origin")
        if (
            len(request.headers.getlist("host")) != 1
            or len(request.headers.getlist("origin")) > 1
            or not valid_host
            or not loopback
            or (origin is not None and origin not in ALLOWED_ORIGINS)
        ):
            response = reject(403, "UNTRUSTED_ORIGIN", "Untrusted request source")
        elif (
            request.url.path != "/health"
            and not (request.method == "OPTIONS" and origin in ALLOWED_ORIGINS)
            and not self.authenticated(request)
        ):
            response = reject(401, "UNAUTHORIZED", "Authentication required")
        else:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - do not expose unexpected exception contents
                response = reject(500, "INTERNAL_ERROR", "Internal error")
        response.headers["X-Request-ID"] = request_id
        return response
