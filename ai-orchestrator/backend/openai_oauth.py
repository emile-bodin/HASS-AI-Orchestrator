"""Sign in with ChatGPT OAuth for the Home Assistant add-on.

This is the open-source Sign in with ChatGPT flow. Tokens are stored only in
/data/openai_oauth.json (or the injected test path), never returned by API
status endpoints, and access tokens are refreshed just before inference.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
DEFAULT_SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


class OpenAIOAuthStore:
    def __init__(self, path: str | Path = "/data/openai_oauth.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = self._read()

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, value: Dict[str, Any]) -> None:
        self._data = value
        fd, tmp_name = tempfile.mkstemp(prefix="openai-oauth-", dir=str(self.path.parent))
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, self.path)
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _host_id(self) -> str:
        host_id = str(self._data.get("ext_agent_host_id") or "")
        if not host_id:
            host_id = str(secrets.token_urlsafe(24))
            self._data["ext_agent_host_id"] = host_id
            self._write(self._data)
        return host_id

    def begin_authorization(self, redirect_uri: str, agent_name: str = "HASS-AI-Orchestrator") -> Dict[str, Any]:
        redirect_uri = redirect_uri.strip()
        if not redirect_uri:
            raise ValueError("openai OAuth redirect URI is required")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        client_id = str(self._data.get("client_id") or "dynamic_agent_client")
        pending = {
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "created_at": int(time.time()),
        }
        self._data["pending"] = pending
        self._write(self._data)
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": DEFAULT_SCOPE,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "ext_agent_host_id": self._host_id(),
        }
        if client_id == "dynamic_agent_client":
            params["agent_name_hint"] = agent_name
        return {
            "authorization_url": f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}",
            "state": state,
            "expires_at": int(time.time()) + 600,
        }

    async def complete_callback(self, code: str, state: str, issued_client_id: Optional[str] = None) -> Dict[str, Any]:
        pending = self._data.get("pending") or {}
        if int(pending.get("created_at") or 0) < int(time.time()) - 600:
            raise ValueError("OAuth authorization attempt expired")
        if not code or not state or not secrets.compare_digest(state, str(pending.get("state") or "")):
            raise ValueError("OAuth state mismatch or missing authorization code")
        pending_client_id = str(pending.get("client_id") or "")
        if issued_client_id and pending_client_id != "dynamic_agent_client" and issued_client_id != pending_client_id:
            raise ValueError("OAuth client_id does not match the pending authorization")
        client_id = str(issued_client_id or pending_client_id or "")
        if client_id == "dynamic_agent_client":
            raise ValueError("OpenAI did not return an issued client_id")
        response = await self._exchange_code(code, client_id, pending)
        self._accept_tokens(response, client_id, pending)
        return self.status()

    async def _exchange_code(self, code: str, client_id: str, pending: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": pending["code_verifier"],
            "redirect_uri": pending["redirect_uri"],
            "resource": RESOURCE,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            return response.json()

    async def _refresh(self, refresh_token: str, client_id: str) -> Dict[str, Any]:
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": RESOURCE,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            return response.json()

    def _accept_tokens(self, response: Dict[str, Any], client_id: str, pending: Dict[str, Any]) -> None:
        access_token = str(response.get("access_token") or "")
        refresh_token = str(response.get("refresh_token") or self._data.get("refresh_token") or "")
        scope = str(response.get("scope") or "")
        if not access_token:
            raise ValueError("OpenAI OAuth response did not contain an access_token")
        if REQUIRED_SCOPE not in scope.split():
            raise ValueError("OpenAI OAuth access did not grant ChatGPT plan inference scope")
        expires_in = int(response.get("expires_in") or 3600)
        data = dict(self._data)
        data.update({
            "client_id": client_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scope": scope,
            "expires_at": int(time.time()) + max(60, expires_in),
            "id_token": response.get("id_token", data.get("id_token", "")),
            "pending": None,
        })
        identity = _decode_jwt_payload(str(data.get("id_token") or ""))
        data["account"] = {k: identity[k] for k in ("sub", "email", "name") if k in identity}
        self._write(data)

    async def get_access_token(self) -> str:
        access_token = str(self._data.get("access_token") or "")
        expires_at = int(self._data.get("expires_at") or 0)
        if access_token and expires_at > int(time.time()) + 120:
            return access_token
        refresh_token = str(self._data.get("refresh_token") or "")
        client_id = str(self._data.get("client_id") or "")
        if not refresh_token or not client_id:
            raise RuntimeError("OpenAI OAuth is not authenticated")
        response = await self._refresh(refresh_token, client_id)
        self._accept_tokens(response, client_id, self._data.get("pending") or {})
        return str(self._data.get("access_token") or "")

    def get_access_token_sync(self) -> str:
        access_token = str(self._data.get("access_token") or "")
        expires_at = int(self._data.get("expires_at") or 0)
        if access_token and expires_at > int(time.time()) + 120:
            return access_token
        refresh_token = str(self._data.get("refresh_token") or "")
        client_id = str(self._data.get("client_id") or "")
        if not refresh_token or not client_id:
            raise RuntimeError("OpenAI OAuth is not authenticated")
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": RESOURCE,
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            token_response = response.json()
        self._accept_tokens(token_response, client_id, self._data.get("pending") or {})
        return str(self._data.get("access_token") or "")

    def status(self) -> Dict[str, Any]:
        scopes = str(self._data.get("scope") or "").split()
        expires_at = int(self._data.get("expires_at") or 0)
        return {
            "authenticated": bool(self._data.get("access_token")) and REQUIRED_SCOPE in scopes,
            "client_id": self._data.get("client_id"),
            "account": self._data.get("account") or {},
            "scope_granted": REQUIRED_SCOPE in scopes,
            "expires_at": expires_at or None,
            "expires_in": max(0, expires_at - int(time.time())) if expires_at else 0,
        }

    def logout(self) -> Dict[str, Any]:
        keep = {"ext_agent_host_id": self._data.get("ext_agent_host_id")}
        self._write({k: v for k, v in keep.items() if v})
        return self.status()
