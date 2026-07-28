"""Persistent OAuth provider for FastMCP - survives server restarts.

Stores DCR clients and refresh tokens in SQLite.  When MCP_ACCESS_PIN is set,
a PIN form is shown to the user during the OAuth authorization step so that
only people who know the PIN can authorize new connections.

Usage:
    from persistent_oauth import PersistentOAuthProvider, register_pin_routes

    auth = PersistentOAuthProvider(
        base_url=base_url,
        db_path="/path/to/oauth_state.db",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    # Add PIN routes to the FastMCP app (call before mcp.run)
    register_pin_routes(mcp, auth)

    mcp.auth = auth
    mcp.run(transport="streamable-http", ...)

Set MCP_ACCESS_PIN in the server's .env to enable PIN protection.
"""

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    OAuthToken,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id   TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token       TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    scopes      TEXT NOT NULL,
    expires_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_refresh_client ON oauth_refresh_tokens(client_id);
"""


def _serialize_client(client: OAuthClientInformationFull) -> str:
    return client.model_dump_json()


def _deserialize_client(data: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull.model_validate_json(data)


class PersistentOAuthProvider(InMemoryOAuthProvider):
    """OAuth provider with SQLite persistence and optional PIN-based access control."""

    def __init__(self, *args: Any, db_path: str | Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._db_path = str(db_path)
        self._pending_auth: dict[str, dict[str, Any]] = {}
        self._init_db()
        self._load_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _load_state(self) -> None:
        with self._conn() as conn:
            for row in conn.execute("SELECT client_id, data FROM oauth_clients"):
                try:
                    client = _deserialize_client(row["data"])
                    self.clients[row["client_id"]] = client
                except Exception:
                    continue

            now = int(time.time())
            conn.execute(
                "DELETE FROM oauth_refresh_tokens "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()

            for row in conn.execute(
                "SELECT token, client_id, scopes, expires_at FROM oauth_refresh_tokens"
            ):
                scopes = json.loads(row["scopes"])
                self.refresh_tokens[row["token"]] = RefreshToken(
                    token=row["token"],
                    client_id=row["client_id"],
                    scopes=scopes,
                    expires_at=row["expires_at"],
                )

    def _valid_pins(self) -> list[str]:
        """Return list of valid PINs (comma-separated MCP_ACCESS_PIN)."""
        raw = os.environ.get("MCP_ACCESS_PIN") or ""
        return [p.strip() for p in raw.split(",") if p.strip()]

    def _access_pin(self) -> str | None:
        """Return first configured PIN, or None if not set."""
        pins = self._valid_pins()
        return pins[0] if pins else None

    # ------------------------------------------------------------------
    # PIN-based authorization flow
    # ------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pin = self._access_pin()
        if not pin:
            # No PIN configured — auto-approve (backward compatible)
            return await super().authorize(client, params)

        # Store pending authorization and redirect user to PIN form
        token = secrets.token_urlsafe(32)
        self._pending_auth[token] = {
            "client_id": client.client_id,
            "params": params,
            "expires_at": time.time() + 300,  # 5-minute window
        }
        base = str(self.base_url).rstrip("/")
        return f"{base}/pin?pending={token}"

    def _complete_auth(self, pending_token: str) -> str | None:
        """Issue an auth code for a validated pending request.

        Returns the redirect URI with the code appended, or None if the
        pending token is missing or expired.
        """
        data = self._pending_auth.pop(pending_token, None)
        if not data or data["expires_at"] < time.time():
            return None

        client = self.clients.get(data["client_id"])
        if not client:
            return None

        params: AuthorizationParams = data["params"]
        scopes_list = list(params.scopes) if params.scopes else []
        if client.scope:
            allowed = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in allowed]

        code = f"auth_code_{secrets.token_hex(16)}"
        self.auth_codes[code] = AuthorizationCode(
            code=code,
            client_id=client.client_id,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=scopes_list,
            expires_at=time.time() + 300,
            code_challenge=params.code_challenge,
        )
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    # ------------------------------------------------------------------
    # SQLite persistence — override InMemoryOAuthProvider
    # ------------------------------------------------------------------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        if client_info.client_id is None:
            return
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_clients(client_id, data, created_at) VALUES(?,?,?)",
                (
                    client_info.client_id,
                    _serialize_client(client_info),
                    int(time.time()),
                ),
            )
            conn.commit()

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        token = await super().exchange_authorization_code(client, authorization_code)
        self._persist_refresh_token_from_oauth_token(client, token)
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        old_token_str = refresh_token.token
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM oauth_refresh_tokens WHERE token = ?", (old_token_str,)
            )
            conn.commit()
        self._persist_refresh_token_from_oauth_token(client, token)
        return token

    def _persist_refresh_token_from_oauth_token(
        self, client: OAuthClientInformationFull, token: OAuthToken
    ) -> None:
        if not token.refresh_token:
            return
        rt_obj = self.refresh_tokens.get(token.refresh_token)
        if rt_obj is None:
            return
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_refresh_tokens"
                "(token, client_id, scopes, expires_at) VALUES(?,?,?,?)",
                (
                    rt_obj.token,
                    rt_obj.client_id,
                    json.dumps(list(rt_obj.scopes)),
                    rt_obj.expires_at,
                ),
            )
            conn.commit()

    def _revoke_internal(
        self,
        access_token_str: str | None = None,
        refresh_token_str: str | None = None,
    ) -> None:
        super()._revoke_internal(
            access_token_str=access_token_str, refresh_token_str=refresh_token_str
        )
        if refresh_token_str:
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM oauth_refresh_tokens WHERE token = ?",
                    (refresh_token_str,),
                )
                conn.commit()


# ---------------------------------------------------------------------------
# HTML PIN form
# ---------------------------------------------------------------------------

def _pin_form_html(pending: str, error: str = "") -> str:
    error_block = f'<p class="error">{error}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accès sécurisé – MCP Server</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #f0f2f5;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    margin: 0;
  }}
  .card {{
    background: #fff;
    padding: 2.5rem 2rem;
    border-radius: 14px;
    box-shadow: 0 4px 24px rgba(0,0,0,.10);
    max-width: 380px;
    width: 92%;
    text-align: center;
  }}
  .lock {{ font-size: 2.5rem; margin-bottom: .75rem; }}
  h1 {{
    font-size: 1.25rem;
    font-weight: 700;
    color: #111;
    margin: 0 0 .4rem;
  }}
  .subtitle {{
    color: #666;
    font-size: .9rem;
    margin: 0 0 1.8rem;
    line-height: 1.4;
  }}
  input[type=password] {{
    width: 100%;
    padding: .8rem 1rem;
    font-size: 1.25rem;
    letter-spacing: .3em;
    border: 2px solid #e0e0e0;
    border-radius: 10px;
    outline: none;
    text-align: center;
    transition: border-color .15s;
  }}
  input[type=password]:focus {{ border-color: #6366f1; }}
  button {{
    width: 100%;
    margin-top: 1rem;
    padding: .85rem;
    font-size: 1rem;
    font-weight: 600;
    background: #6366f1;
    color: #fff;
    border: none;
    border-radius: 10px;
    cursor: pointer;
    transition: background .15s;
  }}
  button:hover {{ background: #4f46e5; }}
  .error {{
    color: #dc2626;
    font-size: .875rem;
    margin: .75rem 0 0;
    font-weight: 500;
  }}
</style>
</head>
<body>
<div class="card">
  <div class="lock">&#128274;</div>
  <h1>Accès sécurisé</h1>
  <p class="subtitle">Entrez le code PIN pour autoriser<br>l'accès à ce serveur MCP.</p>
  <form method="POST" action="/pin">
    <input type="hidden" name="pending" value="{pending}">
    <input type="password" name="pin" placeholder="&#9679;&#9679;&#9679;&#9679;"
           autofocus autocomplete="off">
    {error_block}
    <button type="submit">Autoriser l&rsquo;accès</button>
  </form>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_pin_routes(mcp: Any, provider: PersistentOAuthProvider) -> None:
    """Register /pin routes and RFC 9728 well-known discovery route.

    ChatGPT queries /.well-known/oauth-protected-resource (root, no path suffix)
    to discover the MCP endpoint.  FastMCP registers it at
    /.well-known/oauth-protected-resource/mcp instead, causing a 404 and
    breaking ChatGPT's tool discovery.  We add the root route manually.

    Call this before mcp.run() in each server that uses PersistentOAuthProvider.
    """
    import json as _json
    from starlette.responses import JSONResponse as _JSONResponse

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET", "OPTIONS"])
    async def protected_resource_meta(request: Request) -> Response:
        base = str(provider.base_url).rstrip("/")
        data = {
            "resource": base + "/",
            "authorization_servers": [base + "/"],
            "bearer_methods_supported": ["header"],
        }
        return _JSONResponse(
            data,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Cache-Control": "no-store",
            },
        )

    @mcp.custom_route("/pin", methods=["GET"])
    async def pin_get(request: Request) -> Response:
        pending = request.query_params.get("pending", "")
        if not pending or pending not in provider._pending_auth:
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:2rem'>Lien invalide ou expiré."
                " Relancez la connexion depuis votre application.</h2>",
                status_code=400,
            )
        return HTMLResponse(_pin_form_html(pending))

    @mcp.custom_route("/pin", methods=["POST"])
    async def pin_post(request: Request) -> Response:
        form = await request.form()
        pending = str(form.get("pending", ""))
        entered = str(form.get("pin", ""))

        valid_pins = provider._valid_pins()
        if not valid_pins:
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:2rem'>"
                "Erreur de configuration : MCP_ACCESS_PIN non défini.</h2>",
                status_code=500,
            )

        if not pending or pending not in provider._pending_auth:
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:2rem'>Lien invalide ou expiré."
                " Relancez la connexion depuis votre application.</h2>",
                status_code=400,
            )

        if entered not in valid_pins:
            # Re-issue a fresh pending token so the user can retry
            new_token = secrets.token_urlsafe(32)
            provider._pending_auth[new_token] = provider._pending_auth.pop(pending)
            return HTMLResponse(_pin_form_html(new_token, "PIN incorrect — réessayez."))

        redirect_url = provider._complete_auth(pending)
        if not redirect_url:
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:2rem'>"
                "Session expirée. Relancez la connexion depuis votre application.</h2>",
                status_code=400,
            )

        return RedirectResponse(
            url=redirect_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
