import base64
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from md2hwpx import oauth, web

GITLAB = "http://gitlab.internal"


@pytest.fixture
def client(monkeypatch):
    """A browser on a web service set up for GitLab only, with no sessions yet."""
    monkeypatch.setattr(oauth, "_sessions", {})
    for name in ("GITLAB", "GITEA"):
        for key in ("URL", "CLIENT_ID", "CLIENT_SECRET"):
            monkeypatch.delenv(f"MD2HWPX_{name}_{key}", raising=False)
    monkeypatch.setenv("MD2HWPX_BASE_URL", "http://md2hwpx.internal:8000/")
    monkeypatch.setenv("MD2HWPX_GITLAB_URL", GITLAB + "/")
    monkeypatch.setenv("MD2HWPX_GITLAB_CLIENT_ID", "app-id")
    monkeypatch.setenv("MD2HWPX_GITLAB_CLIENT_SECRET", "app-secret")
    return TestClient(web.app)


def _sign_in(client, monkeypatch, token="user-token", expires_in=7200) -> list:
    """Sign in to GitLab, the server standing in for itself; the token requests it got."""
    exchanges = []

    def fake_exchange(server, code):
        exchanges.append((server, code))
        return token, expires_in

    monkeypatch.setattr(oauth, "_exchange", fake_exchange)
    login = client.get("/login/gitlab", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    back = client.get("/oauth/gitlab/callback", params={"code": "the-code", "state": state}, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "http://md2hwpx.internal:8000/"
    return exchanges


def _fake_git(monkeypatch, returncode=0) -> list:
    """Stand in for git: the (command, env) of each run."""
    runs = []

    def fake_run(command, **kwargs):
        runs.append((command, kwargs["env"]))
        (Path(command[-1]) / "guide.md").write_text("# 안내\n", encoding="utf-8")
        return web.subprocess.CompletedProcess(command, returncode, "", "fatal: Authentication failed")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    return runs


def _clone(client, repo):
    return client.post("/convert", data={"source": "git", "repo": repo})


def test_status_lists_the_servers_set_up(client, monkeypatch):
    assert client.get("/auth").json() == [{"name": "gitlab", "label": "GitLab", "url": GITLAB, "signed_in": False}]
    # A server is set up only with all its settings, and the web service's own address.
    monkeypatch.setenv("MD2HWPX_GITEA_URL", "http://gitea.internal")
    monkeypatch.setenv("MD2HWPX_GITEA_CLIENT_ID", "id")
    assert [s["name"] for s in client.get("/auth").json()] == ["gitlab"]
    monkeypatch.delenv("MD2HWPX_BASE_URL")
    assert client.get("/auth").json() == []


def test_login_goes_to_the_server(client):
    response = client.get("/login/gitlab", follow_redirects=False)

    url = urlsplit(response.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == GITLAB + "/oauth/authorize"
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert query["client_id"] == "app-id" and query["response_type"] == "code" and query["scope"] == "read_repository"
    assert query["redirect_uri"] == "http://md2hwpx.internal:8000/oauth/gitlab/callback"
    assert query["state"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/login/gitea", follow_redirects=False).status_code == 404  # not set up


def test_callback_signs_in(client, monkeypatch):
    exchanges = _sign_in(client, monkeypatch)

    assert exchanges[0][0].provider.name == "gitlab" and exchanges[0][1] == "the-code"
    assert client.get("/auth").json()[0]["signed_in"] is True


@pytest.mark.parametrize("state", ["", "not-the-state"])
def test_callback_needs_the_state_of_the_login(client, monkeypatch, state):
    monkeypatch.setattr(oauth, "_exchange", lambda server, code: pytest.fail("no token request"))
    client.get("/login/gitlab", follow_redirects=False)

    back = client.get("/oauth/gitlab/callback", params={"code": "c", "state": state}, follow_redirects=False)

    assert "login_error=" in back.headers["location"]
    assert client.get("/auth").json()[0]["signed_in"] is False


def test_callback_when_the_user_declined(client, monkeypatch):
    login = client.get("/login/gitlab", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]

    params = {"error": "access_denied", "state": state}
    back = client.get("/oauth/gitlab/callback", params=params, follow_redirects=False)

    error = parse_qs(urlsplit(back.headers["location"]).query)["login_error"][0]
    assert error == "GitLab 로그인이 취소되었거나 실패했습니다. access_denied"


def test_clone_as_the_signed_in_user(client, monkeypatch, tmp_path):
    _sign_in(client, monkeypatch)
    runs = _fake_git(monkeypatch)

    assert _clone(client, GITLAB + "/team/manual.git").status_code == 200

    command, env = runs[0]
    expected = "Authorization: Basic " + base64.b64encode(b"oauth2:user-token").decode()
    assert (env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == (
        "1", "http.extraHeader", expected
    )
    assert not any("user-token" in part or "Authorization" in part for part in command)


@pytest.mark.parametrize("repo", [
    "http://github.example/team/manual.git",  # another server
    "http://gitlab.internal.example/team/manual.git",  # the name continues past the server's
    "http://gitlab.internal@evil.example/team/manual.git",  # the server's name as the user
    "https://gitlab.internal/team/manual.git",  # another scheme
])
def test_token_goes_only_to_its_server(client, monkeypatch, repo):
    _sign_in(client, monkeypatch)
    runs = _fake_git(monkeypatch)

    _clone(client, repo)

    assert "GIT_CONFIG_VALUE_0" not in runs[0][1]


def test_without_signing_in_clones_anonymously_and_hints(client, monkeypatch):
    runs = _fake_git(monkeypatch, returncode=128)

    response = _clone(client, GITLAB + "/team/manual.git")

    assert "GIT_CONFIG_VALUE_0" not in runs[0][1]
    assert response.json()["detail"].endswith("비공개 저장소라면 GitLab 에 로그인하세요.")


def test_expired_token_is_not_used(client, monkeypatch):
    _sign_in(client, monkeypatch, expires_in=7200)
    monkeypatch.setattr(oauth.time, "time", lambda: 10**12)  # long after
    runs = _fake_git(monkeypatch)

    _clone(client, GITLAB + "/team/manual.git")

    assert "GIT_CONFIG_VALUE_0" not in runs[0][1]
    assert client.get("/auth").json()[0]["signed_in"] is False


def test_logout(client, monkeypatch):
    _sign_in(client, monkeypatch)

    assert client.post("/logout/gitlab").status_code == 204
    assert client.get("/auth").json()[0]["signed_in"] is False


def test_sessions_are_per_browser(client, monkeypatch):
    _sign_in(client, monkeypatch)
    runs = _fake_git(monkeypatch)

    _clone(TestClient(web.app), GITLAB + "/team/manual.git")  # another browser, without the cookie

    assert "GIT_CONFIG_VALUE_0" not in runs[0][1]


@pytest.fixture
def token_server():
    """A token endpoint on localhost: answers with the JSON put in .answer, and keeps the form it got."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            server.form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            body = json.dumps(server.answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _server(url):
    return oauth._Server(oauth.PROVIDERS["gitea"], url, "app-id", "app-secret", "http://md2hwpx/oauth/gitea/callback")


def test_exchange(token_server):
    token_server.answer = {"access_token": "tok", "token_type": "bearer", "expires_in": 3600}

    url = f"http://127.0.0.1:{token_server.server_port}"
    assert oauth._exchange(_server(url), "the-code") == ("tok", 3600)
    assert {key: values[0] for key, values in token_server.form.items()} == {
        "client_id": "app-id", "client_secret": "app-secret", "code": "the-code",
        "grant_type": "authorization_code", "redirect_uri": "http://md2hwpx/oauth/gitea/callback",
    }


def test_exchange_without_a_token(token_server):
    token_server.answer = {"error": "invalid_grant"}

    with pytest.raises(OSError, match="invalid_grant"):
        oauth._exchange(_server(f"http://127.0.0.1:{token_server.server_port}"), "old-code")
