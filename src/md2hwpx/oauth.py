"""Sign in to GitLab / Gitea with OAuth2, so the web service clones a private repository as that user.

Set up by environment variables (see .env.example): MD2HWPX_BASE_URL, the web service's own address, and
MD2HWPX_GITLAB_* / MD2HWPX_GITEA_* (URL, CLIENT_ID, CLIENT_SECRET) for each server it signs in to.
A user's token is kept in this process's memory, under a random session id in a cookie, and is sent only
to the server it came from.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

COOKIE = "md2hwpx_session"
SESSION_TTL = 12 * 60 * 60  # seconds a session lasts unused
TOKEN_TIMEOUT = 30  # seconds for the token request


@dataclass(frozen=True)
class Provider:
    name: str  # in the URLs and in the environment variable names
    label: str
    authorize_path: str
    token_path: str
    scope: str  # read access to repositories


PROVIDERS = {provider.name: provider for provider in (
    Provider("gitlab", "GitLab", "/oauth/authorize", "/oauth/token", "read_repository"),
    Provider("gitea", "Gitea", "/login/oauth/authorize", "/login/oauth/access_token", "read:repository"),
)}


@dataclass(frozen=True)
class _Server:
    """A provider as this instance is set up for it."""

    provider: Provider
    url: str  # without a trailing "/"
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass
class _Session:
    used: float  # time.time() of the last request
    states: dict[str, str] = field(default_factory=dict)  # OAuth state -> provider name, for a sign-in under way
    tokens: dict[str, tuple[str, float]] = field(default_factory=dict)  # provider name -> (token, expiry time)

    def token(self, name: str) -> str | None:
        token, expires = self.tokens.get(name, ("", 0.0))
        return token if time.time() < expires else None


_sessions: dict[str, _Session] = {}
router = APIRouter()


def base_url() -> str:
    """The web service's own address, as the browser reaches it, without a trailing "/"."""
    return os.environ.get("MD2HWPX_BASE_URL", "").strip().rstrip("/")


def _server(provider: Provider) -> _Server | None:
    """*provider* as set up by the environment, or None if any of its settings is missing."""
    prefix = f"MD2HWPX_{provider.name.upper()}_"
    url, client_id, secret = (os.environ.get(prefix + key, "").strip() for key in ("URL", "CLIENT_ID", "CLIENT_SECRET"))
    if not (url and client_id and secret and base_url()):
        return None
    return _Server(provider, url.rstrip("/"), client_id, secret, f"{base_url()}/oauth/{provider.name}/callback")


def _servers() -> list[_Server]:
    return [server for provider in PROVIDERS.values() if (server := _server(provider))]


def server_for(repo: str) -> _Server | None:
    """The set-up server *repo* is on. The trailing "/" keeps "http://git.a.b/x" from passing for "http://git.a"."""
    return next((server for server in _servers() if repo.startswith(server.url + "/")), None)


def _session(request: Request) -> _Session | None:
    session = _sessions.get(request.cookies.get(COOKIE, ""))
    if session is None or time.time() - session.used > SESSION_TTL:
        return None
    session.used = time.time()
    return session


def _new_session(response: Response) -> _Session:
    now = time.time()
    for sid in [sid for sid, session in _sessions.items() if now - session.used > SESSION_TTL]:
        del _sessions[sid]
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = session = _Session(now)
    response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", secure=base_url().startswith("https://"))
    return session


def git_header(request: Request, server: _Server) -> str | None:
    """The http.extraHeader that clones from *server* as the signed-in user, or None if not signed in to it.

    Both GitLab and Gitea take an OAuth2 token as the password of HTTP basic authentication, user "oauth2".
    """
    session = _session(request)
    token = session.token(server.provider.name) if session else None
    if token is None:
        return None
    return "Authorization: Basic " + base64.b64encode(f"oauth2:{token}".encode()).decode()


@router.get("/auth")
def status(request: Request) -> list[dict]:
    """The servers this instance signs in to, and whether the user is signed in to each."""
    session = _session(request)
    return [
        {"name": s.provider.name, "label": s.provider.label, "url": s.url,
         "signed_in": bool(session and session.token(s.provider.name))}
        for s in _servers()
    ]


@router.get("/login/{name}")
def login(name: str, request: Request) -> Response:
    """Send the browser to the server's sign-in page; it comes back to callback()."""
    server = _server(PROVIDERS[name]) if name in PROVIDERS else None
    if server is None:
        raise HTTPException(404, f"로그인할 수 없는 서버입니다: {name}")
    state = secrets.token_urlsafe(16)  # ties the callback to this sign-in, so no other site can start one
    query = urllib.parse.urlencode({
        "client_id": server.client_id, "redirect_uri": server.redirect_uri, "response_type": "code",
        "state": state, "scope": server.provider.scope,
    })
    response = RedirectResponse(f"{server.url}{server.provider.authorize_path}?{query}", status_code=302)
    session = _session(request) or _new_session(response)
    session.states[state] = name
    return response


@router.get("/oauth/{name}/callback")
def callback(name: str, request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    """The server sends the browser back here with a code, which is traded for the user's token."""
    session = _session(request)
    server = _server(PROVIDERS[name]) if name in PROVIDERS else None
    if server is None or session is None or session.states.pop(state, None) != name:
        return _back("로그인 요청이 올바르지 않거나 오래되었습니다. 다시 로그인하세요.")
    if error or not code:
        return _back(f"{server.provider.label} 로그인이 취소되었거나 실패했습니다. {error}".strip())
    try:
        token, expires_in = _exchange(server, code)
    except OSError as failure:
        return _back(f"{server.provider.label} 에서 토큰을 받지 못했습니다: {failure}")
    session.tokens[name] = (token, time.time() + expires_in if expires_in else float("inf"))
    return _back()


@router.post("/logout/{name}")
def logout(name: str, request: Request) -> Response:
    """Forget the user's token for the server *name*."""
    session = _session(request)
    if session is not None:
        session.tokens.pop(name, None)
    return Response(status_code=204)


def _back(error: str = "") -> Response:
    """Back to the page, which shows *error* if there is one."""
    query = "?" + urllib.parse.urlencode({"login_error": error}) if error else ""
    return RedirectResponse(f"{base_url()}/{query}", status_code=303)


def _exchange(server: _Server, code: str) -> tuple[str, int | None]:
    """Trade the sign-in *code* for (access token, seconds it lasts). Raises OSError if that fails."""
    data = urllib.parse.urlencode({
        "client_id": server.client_id, "client_secret": server.client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": server.redirect_uri,
    }).encode()
    request = urllib.request.Request(
        server.url + server.provider.token_path, data=data, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TOKEN_TIMEOUT) as answer:  # HTTPError, URLError: OSError
        try:
            body = json.load(answer)
        except ValueError:
            raise OSError("응답이 JSON 이 아닙니다") from None
    if not isinstance(body, dict) or not body.get("access_token"):
        raise OSError(f"응답에 access_token 이 없습니다 ({body.get('error', '') if isinstance(body, dict) else ''})")
    expires_in = body.get("expires_in")
    return body["access_token"], int(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else None
