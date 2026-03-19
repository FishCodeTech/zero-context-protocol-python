from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


def pkce_s256_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def now_ts() -> float:
    return time.time()


@dataclass
class OAuthClient:
    client_id: str
    redirect_uris: tuple[str, ...]
    client_secret: str | None = None
    name: str | None = None


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    state: str | None = None
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    expires_at: float = field(default_factory=lambda: now_ts() + 600)

    @property
    def expired(self) -> bool:
        return now_ts() >= self.expires_at


@dataclass
class AccessToken:
    token: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at: float

    @property
    def expired(self) -> bool:
        return now_ts() >= self.expires_at


@dataclass
class RefreshToken:
    token: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at: float

    @property
    def expired(self) -> bool:
        return now_ts() >= self.expires_at


class OAuthProvider(Protocol):
    def get_client(self, client_id: str) -> OAuthClient | None: ...

    def save_client(self, client: OAuthClient) -> None: ...

    def save_authorization_code(self, code: AuthorizationCode) -> None: ...

    def pop_authorization_code(self, code: str) -> AuthorizationCode | None: ...

    def save_access_token(self, token: AccessToken) -> None: ...

    def get_access_token(self, token: str) -> AccessToken | None: ...

    def save_refresh_token(self, token: RefreshToken) -> None: ...

    def get_refresh_token(self, token: str) -> RefreshToken | None: ...

    def revoke_token(self, token: str) -> None: ...

    def purge_expired(self) -> None: ...


class InMemoryOAuthProvider:
    def __init__(self, *, default_clients: tuple[OAuthClient, ...] = ()) -> None:
        self._clients: dict[str, OAuthClient] = {client.client_id: client for client in default_clients}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

    def get_client(self, client_id: str) -> OAuthClient | None:
        return self._clients.get(client_id)

    def save_client(self, client: OAuthClient) -> None:
        self._clients[client.client_id] = client

    def save_authorization_code(self, code: AuthorizationCode) -> None:
        self._codes[code.code] = code

    def pop_authorization_code(self, code: str) -> AuthorizationCode | None:
        value = self._codes.pop(code, None)
        if value is None or value.expired:
            return None
        return value

    def save_access_token(self, token: AccessToken) -> None:
        self._access_tokens[token.token] = token

    def get_access_token(self, token: str) -> AccessToken | None:
        value = self._access_tokens.get(token)
        if value is None or value.expired:
            return None
        return value

    def save_refresh_token(self, token: RefreshToken) -> None:
        self._refresh_tokens[token.token] = token

    def get_refresh_token(self, token: str) -> RefreshToken | None:
        value = self._refresh_tokens.get(token)
        if value is None or value.expired:
            return None
        return value

    def revoke_token(self, token: str) -> None:
        self._access_tokens.pop(token, None)
        self._refresh_tokens.pop(token, None)

    def purge_expired(self) -> None:
        self._codes = {key: value for key, value in self._codes.items() if not value.expired}
        self._access_tokens = {key: value for key, value in self._access_tokens.items() if not value.expired}
        self._refresh_tokens = {key: value for key, value in self._refresh_tokens.items() if not value.expired}


class SQLiteOAuthProvider:
    def __init__(self, database_path: str | Path, *, default_clients: tuple[OAuthClient, ...] = ()) -> None:
        self.database_path = str(database_path)
        self._lock = threading.Lock()
        self._init_db()
        for client in default_clients:
            self.save_client(client)

    def get_client(self, client_id: str) -> OAuthClient | None:
        row = self._fetchone("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,))
        if row is None:
            return None
        return OAuthClient(
            client_id=row["client_id"],
            client_secret=row["client_secret"],
            redirect_uris=tuple(json.loads(row["redirect_uris"])),
            name=row["name"],
        )

    def save_client(self, client: OAuthClient) -> None:
        self._execute(
            """
            INSERT INTO oauth_clients(client_id, client_secret, redirect_uris, name)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                client_secret=excluded.client_secret,
                redirect_uris=excluded.redirect_uris,
                name=excluded.name
            """,
            (client.client_id, client.client_secret, json.dumps(list(client.redirect_uris)), client.name),
        )

    def save_authorization_code(self, code: AuthorizationCode) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO oauth_codes(code, client_id, redirect_uri, scopes, state, code_challenge, code_challenge_method, expires_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code.code,
                code.client_id,
                code.redirect_uri,
                json.dumps(list(code.scopes)),
                code.state,
                code.code_challenge,
                code.code_challenge_method,
                code.expires_at,
            ),
        )

    def pop_authorization_code(self, code: str) -> AuthorizationCode | None:
        row = self._fetchone("SELECT * FROM oauth_codes WHERE code = ?", (code,))
        if row is None:
            return None
        self._execute("DELETE FROM oauth_codes WHERE code = ?", (code,))
        value = AuthorizationCode(
            code=row["code"],
            client_id=row["client_id"],
            redirect_uri=row["redirect_uri"],
            scopes=tuple(json.loads(row["scopes"])),
            state=row["state"],
            code_challenge=row["code_challenge"],
            code_challenge_method=row["code_challenge_method"],
            expires_at=row["expires_at"],
        )
        if value.expired:
            return None
        return value

    def save_access_token(self, token: AccessToken) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO oauth_access_tokens(token, client_id, scopes, expires_at)
            VALUES(?, ?, ?, ?)
            """,
            (token.token, token.client_id, json.dumps(list(token.scopes)), token.expires_at),
        )

    def get_access_token(self, token: str) -> AccessToken | None:
        row = self._fetchone("SELECT * FROM oauth_access_tokens WHERE token = ?", (token,))
        if row is None:
            return None
        value = AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=tuple(json.loads(row["scopes"])),
            expires_at=row["expires_at"],
        )
        if value.expired:
            self.revoke_token(token)
            return None
        return value

    def save_refresh_token(self, token: RefreshToken) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO oauth_refresh_tokens(token, client_id, scopes, expires_at)
            VALUES(?, ?, ?, ?)
            """,
            (token.token, token.client_id, json.dumps(list(token.scopes)), token.expires_at),
        )

    def get_refresh_token(self, token: str) -> RefreshToken | None:
        row = self._fetchone("SELECT * FROM oauth_refresh_tokens WHERE token = ?", (token,))
        if row is None:
            return None
        value = RefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=tuple(json.loads(row["scopes"])),
            expires_at=row["expires_at"],
        )
        if value.expired:
            self.revoke_token(token)
            return None
        return value

    def revoke_token(self, token: str) -> None:
        self._execute("DELETE FROM oauth_access_tokens WHERE token = ?", (token,))
        self._execute("DELETE FROM oauth_refresh_tokens WHERE token = ?", (token,))

    def purge_expired(self) -> None:
        threshold = now_ts()
        self._execute("DELETE FROM oauth_codes WHERE expires_at <= ?", (threshold,))
        self._execute("DELETE FROM oauth_access_tokens WHERE expires_at <= ?", (threshold,))
        self._execute("DELETE FROM oauth_refresh_tokens WHERE expires_at <= ?", (threshold,))

    def _init_db(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_secret TEXT,
                redirect_uris TEXT NOT NULL,
                name TEXT
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                scopes TEXT NOT NULL,
                state TEXT,
                code_challenge TEXT,
                code_challenge_method TEXT,
                expires_at REAL NOT NULL
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                scopes TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                scopes TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        with self._lock:
            with self._connection() as connection:
                connection.execute(query, params)
                connection.commit()

    def _fetchone(self, query: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            with self._connection() as connection:
                row = connection.execute(query, params).fetchone()
        return row


def generate_code(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"
