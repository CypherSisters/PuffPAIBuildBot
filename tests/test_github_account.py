"""Her GitHub account (puffpai.github_account): connect, use, disconnect.

A stubbed GitHub answers on BOTH exits a request can take: the egress door
seam (``egress_doors._send``: the token check, the device sign-in) and the
pinned wire the keyed fetch hop and plain fetches use
(``egress_doors._keyed_wire`` / ``source_fetch._default_transport``). No real
network, no real Credential Manager.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import string
import tarfile
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import pytest

from keys_support import fast_vault, grep_tree, reset_keys, set_posture
from puffpai import api_server, github_account, source_fetch
from puffpai.security import egress_doors, keys, outside_switches

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
DEV = {**AUTH, "X-Puff-Device-Id": "deviceghub000001"}
DEV_B = {**AUTH, "X-Puff-Device-Id": "deviceghub000002"}
PUBLIC = ["140.82.112.5"]
LOGIN = "octo-owner"
SHA = "a" * 40
TEMP = "TEMPDOWNLOADCODE9xQ"


def _token(prefix: str = "ghp_") -> str:
    alphabet = string.ascii_letters + string.digits
    return prefix + "".join(secrets.choice(alphabet) for _ in range(40))


def _repo(name: str, *, private: bool = False, language: str = "TypeScript",
          description: str = "", topics=(), owner: str = LOGIN,
          licence: Optional[str] = "MIT") -> dict:
    return {"name": name, "full_name": f"{owner}/{name}", "owner": {"login": owner},
            "private": private, "description": description, "language": language,
            "topics": list(topics), "default_branch": "main",
            "html_url": f"https://github.com/{owner}/{name}",
            "license": {"spdx_id": licence} if licence else None,
            "stargazers_count": 3, "pushed_at": "2026-09-01T00:00:00Z",
            "archived": False, "fork": False, "size": 10}


def _tarball(commit: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": commit}) as tar:
        data = b"<html><body>garden planner</body></html>"
        info = tarfile.TarInfo(f"{LOGIN}-garden-planner-{commit[:7]}/index.html")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeGitHub:
    """api.github.com, github.com and codeload.github.com, in memory."""

    def __init__(self):
        self.token = ""                       # the one token GitHub accepts
        self.scopes: Optional[str] = None     # X-OAuth-Scopes, None = fine-grained
        self.private_visible = False
        self.mine = [_repo("garden-planner", description="Plan a garden bed by bed"),
                     _repo("secret-garden", private=True, description="My garden notes app")]
        self.starred = [_repo("seed-catalog", description="garden seed catalog web app")]
        self.search_status = 200
        self.search_headers: dict = {}
        self.device_answers: list[dict] = []
        self.calls: list[dict] = []

    # one answer for one request, whichever exit it took
    def answer(self, method: str, url: str, headers: dict, data: Any = None):
        h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "data": dict(data) if isinstance(data, dict) else data})
        parts = urlsplit(url)
        host, path = parts.hostname, parts.path
        authed = bool(self.token) and h.get("authorization") == f"Bearer {self.token}"
        rate = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Reset": "1999999999", "X-RateLimit-Resource": "core"}
        if host == "github.com" and method == "POST":
            if path == "/login/device/code":
                return 200, {"device_code": "dc_" + "z" * 30, "user_code": "WDJB-MJHT",
                             "verification_uri": "https://github.com/login/device",
                             "interval": 5, "expires_in": 900}, {}
            if path == "/login/oauth/access_token":
                return 200, self.device_answers.pop(0), {}
        if host == "codeload.github.com":
            if TEMP not in parts.query:
                return 404, b"", {}
            return 200, _tarball(SHA), {"Content-Type": "application/x-gzip"}
        if host != "api.github.com":
            return 404, {}, {}
        if path == "/search/repositories":
            items = [_repo("garden-planner"), _repo("secret-garden", private=True)]
            return self.search_status, {"items": items}, {
                **rate, "X-RateLimit-Resource": "search", **self.search_headers}
        if path == "/rate_limit":
            limit = 30 if authed else 10
            return 200, {"resources": {
                "core": {"limit": 5000, "remaining": 4990, "reset": 1999999999},
                "search": {"limit": limit, "remaining": limit - 1, "reset": 1999999999}}}, {}
        if self.token and h.get("authorization") and not authed:
            return 401, {"message": "Bad credentials"}, {}
        if path == "/user":
            if not authed:
                return 401, {"message": "Requires authentication"}, {}
            extra = {"X-OAuth-Scopes": self.scopes} if self.scopes is not None else {}
            return 200, {"login": LOGIN, "name": "Octo Owner",
                         "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4"}, \
                {**rate, **extra}
        if path == "/user/repos":
            q = parse_qs(parts.query)
            if q.get("visibility") == ["private"]:
                return 200, [r for r in self.mine if r["private"]] \
                    if self.private_visible else [], rate
            return 200, [r for r in self.mine if self.private_visible or not r["private"]], rate
        if path == f"/users/{LOGIN}/starred":
            return 200, self.starred, rate
        for r in self.mine + self.starred:
            base = f"/repos/{r['full_name']}"
            if r["private"] and not (authed and self.private_visible):
                continue
            if path == base:
                return 200, r, rate
            if path == f"{base}/commits/main":
                return 200, SHA.encode(), rate
            if path == f"{base}/tarball/{SHA}":
                if not authed:
                    return 404, {}, rate
                return 302, b"", {"Location": f"https://codeload.github.com/{r['full_name']}"
                                              f"/legacy.tar.gz/{SHA}?token={TEMP}"}
        return 404, {"message": "Not Found"}, rate

    def saw(self, needle: str) -> bool:
        return any(needle in json.dumps(c, default=str) for c in self.calls)


class ReqResp:
    """What egress_doors._send hands back (a requests-like answer)."""

    def __init__(self, status, body, headers):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Content-Type": "application/json", **headers}
        self.raw = None
        self.request = type("Req", (), {"headers": {}, "body": None})()

    def json(self):
        return json.loads(self.content.decode())

    def close(self):
        pass


class WireResp:
    """What a pinned urllib wire hands back."""

    def __init__(self, status, body, headers):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = dict(headers)

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._body = self._body, b""
        else:
            out, self._body = self._body[:n], self._body[n:]
        return out

    def close(self):
        pass


@pytest.fixture
def gh(monkeypatch, tmp_path):
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    reset_keys()
    set_posture("enumerated")
    github_account._reset_for_tests()
    v = fast_vault(monkeypatch, tmp_path / "vault")
    monkeypatch.setattr(api_server, "_vault_instance", v)
    keys.set_vault_provider(lambda: v)
    fake = FakeGitHub()

    def door_send(method, url, *, pin=None, **kw):
        status, body, headers = fake.answer(method, url, kw.get("headers") or {},
                                            kw.get("data"))
        return ReqResp(status, body, headers)

    def wire(url, headers, timeout):
        status, body, hdrs = fake.answer("GET", url, headers)
        return WireResp(status, body, hdrs)

    monkeypatch.setattr(egress_doors, "_send", door_send)
    monkeypatch.setattr(egress_doors, "_keyed_wire", wire)
    monkeypatch.setattr(source_fetch, "_default_transport", wire)
    monkeypatch.setattr(source_fetch, "_default_resolver", lambda host: PUBLIC)
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(source_fetch, "HOST_MIN_INTERVAL_BY_HOST", {})
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(tmp_path / "apps"))
    source_fetch.clear_caches()
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    fake.client, fake.vault, fake.tmp = client, v, tmp_path
    yield fake
    github_account._reset_for_tests()
    reset_keys()
    source_fetch.clear_caches()


def _state() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def _connect(gh, *, scopes: Optional[str] = "read:user", private: bool = False) -> str:
    gh.token = _token()
    gh.scopes = scopes
    gh.private_visible = private
    r = gh.client.post("/github/token", headers=DEV, json={"token": gh.token})
    assert r.status_code == 200, r.get_json()
    return gh.token


# -- the routes ---------------------------------------------------------------

ROUTES = (("get", "/github/status"), ("get", "/github/token-help"),
          ("post", "/github/token"), ("post", "/github/device/start"),
          ("post", "/github/device/poll"), ("post", "/github/disconnect"))


def test_every_route_needs_the_api_key_and_a_paired_device(gh):
    for method, path in ROUTES:
        assert getattr(gh.client, method)(path).status_code == 401, path
        r = getattr(gh.client, method)(path, headers=AUTH, json={})
        assert r.status_code == 403, path
        assert "paired" in r.get_json()["message"]
    assert gh.calls == []


def test_status_and_help_send_nothing_and_change_nothing(gh):
    before = sorted(os.listdir(_state()))
    st = gh.client.get("/github/status", headers=DEV).get_json()
    assert st["connected"] is False and st["device_available"] is False
    assert st["vault_locked"] is False and st["method"] == "token"
    assert set(st["rate"]) == {"limit", "remaining", "reset"}
    for key in ("login", "name", "avatar_url", "scopes", "can_read_private"):
        assert key in st
    helped = gh.client.get("/github/token-help", headers=DEV).get_json()
    q = parse_qs(urlsplit(helped["url"]).query)
    assert helped["url"].startswith("https://github.com/settings/personal-access-tokens/new?")
    assert q["name"] == ["BuildBot"] and q["contents"] == ["read"] \
        and q["metadata"] == ["read"]
    assert helped["steps"] and all("—" not in s for s in helped["steps"])
    assert gh.calls == []
    assert sorted(os.listdir(_state())) == before


# -- connecting with a token --------------------------------------------------

def test_a_token_is_checked_through_the_bridge_and_kept_only_in_the_vault(gh, caplog):
    caplog.set_level("DEBUG")
    token = _connect(gh)
    st = gh.client.get("/github/status", headers=DEV).get_json()
    assert st["connected"] and st["login"] == LOGIN and st["name"] == "Octo Owner"
    assert st["switched_on"] is True and outside_switches.get("github") is True
    assert st["scopes"] == ["read:user"] and st["extra_scopes"] == [] and st["warning"] == ""
    # the check went to api.github.com /user, with the token in a header only
    checks = [c for c in gh.calls if urlsplit(c["url"]).path == "/user"]
    assert checks and all(urlsplit(c["url"]).hostname == "api.github.com" for c in checks)
    assert not any(token in c["url"] for c in gh.calls)
    # stored in the vault (readable there), nowhere else on disk, in no log
    assert keys._record_for(gh.vault, "github")["value"] == token  # noqa: SLF001
    assert grep_tree(_state(), token) == []
    assert grep_tree(str(gh.tmp / "vault"), token) == []      # encrypted at rest
    assert token not in caplog.text
    assert not os.path.exists(os.path.join(str(gh.tmp / "vault"), "key_github.json"))


def test_the_answer_never_echoes_the_token_and_the_address_never_carries_it(gh):
    gh.token = _token()
    r = gh.client.post("/github/token", headers=DEV, json={"token": gh.token})
    assert gh.token not in r.get_data(as_text=True)
    r = gh.client.post(f"/github/token?token={gh.token}", headers=DEV, json={})
    assert r.status_code == 400 and gh.token not in r.get_data(as_text=True)


def test_a_rejected_token_is_not_kept_and_github_goes_back_off(gh, caplog):
    caplog.set_level("DEBUG")
    gh.token = _token()
    wrong = _token()
    r = gh.client.post("/github/token", headers=DEV, json={"token": wrong})
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert wrong not in body and "not right" in body
    assert github_account.connected() is False
    assert outside_switches.get("github") is not True
    assert grep_tree(_state(), wrong) == [] and wrong not in caplog.text


def test_a_token_that_can_write_is_accepted_and_said_plainly(gh):
    _connect(gh, scopes="repo, read:user", private=False)
    st = github_account.status()
    assert st["extra_scopes"] == ["repo"]
    assert st["warning"] == "This token can also change your projects; BuildBot only reads."
    assert st["can_read_private"] is True        # classic "repo" reads private ones


def test_a_fine_grained_token_says_nothing_and_private_is_probed(gh):
    _connect(gh, scopes=None, private=True)
    st = github_account.status()
    assert st["scopes_known"] is False and st["warning"] == github_account.SCOPES_UNKNOWN
    assert st["can_read_private"] is True


def test_the_token_is_never_kept_outside_the_vault(gh, monkeypatch, tmp_path):
    from keys_support import machine_key_file
    machine_key_file(monkeypatch, tmp_path)
    _connect(gh)
    with pytest.raises(keys.KeyRefused):
        keys.set_background("github", True)
    assert keys.background_file_allowed("key_github.json") is False
    assert keys.key_status("github")["background_possible"] is False
    gh.vault.lock()
    assert keys._record_for(gh.vault, "github") is None   # noqa: SLF001
    st = github_account.status()
    assert st["vault_locked"] is True and st["connected"] is True
    assert github_account.search_key_id() is None


def test_the_keys_page_does_not_offer_or_take_the_github_token(gh):
    body = gh.client.get("/keys", headers=AUTH).get_json()
    assert [k["id"] for k in body["keys"]] == ["brave_search", "marginalia_search"]
    r = gh.client.post("/keys/github/confirm", headers=DEV, json={})
    assert r.status_code == 404


# -- searching ----------------------------------------------------------------

def _searches(gh) -> list[dict]:
    return [c for c in gh.calls if urlsplit(c["url"]).path == "/search/repositories"]


def _auth_of(call: dict) -> str:
    return {k.lower(): v for k, v in call["headers"].items()}.get("authorization", "")


def test_the_search_header_is_there_only_when_connected_open_and_on(gh):
    from puffpai import outside_services
    from puffpai.finding import sites
    sites.github("garden one")
    assert _auth_of(_searches(gh)[-1]) == ""             # not connected
    token = _connect(gh)
    finds = sites.github("garden two")
    assert _auth_of(_searches(gh)[-1]) == f"Bearer {token}"
    assert [f["id"] for f in finds] == [f"github:{LOGIN}/garden-planner"]   # private dropped
    gh.vault.lock()
    sites.github("garden three")
    assert _auth_of(_searches(gh)[-1]) == ""             # vault locked
    from keys_support import PIN
    gh.vault.unlock(PIN)
    outside_services.switch_off("github", by="owner")
    sites.github("garden four")
    assert _auth_of(_searches(gh)[-1]) == ""             # GitHub switched off
    assert grep_tree(_state(), token) == []


def test_github_rate_limit_is_respected_and_does_not_stop_the_token(gh):
    from puffpai.finding import sites
    _connect(gh)
    gh.search_status = 403
    gh.search_headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"}
    assert sites.github("garden five") == []
    sent = len(_searches(gh))
    assert sites.github("garden six") == []
    assert len(_searches(gh)) == sent                     # nothing sent while blocked
    assert github_account.connected() is True             # a rate limit is not "wrong"
    assert github_account.rate_blocked("search") is True


# -- disconnecting --------------------------------------------------------------

def test_disconnect_deletes_the_token_and_says_where_to_revoke(gh):
    token = _connect(gh)
    r = gh.client.post("/github/disconnect", headers=DEV, json={})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    assert body["revoke_url"] == "https://github.com/settings/personal-access-tokens"
    assert body["status"]["connected"] is False
    assert keys._record_for(gh.vault, "github") is None   # noqa: SLF001
    assert outside_switches.get("github") is False
    assert not os.path.exists(os.path.join(_state(), "github", "account.json"))
    assert grep_tree(_state(), token) == []


# -- the device sign-in -----------------------------------------------------------

def test_the_device_sign_in_is_off_by_default(gh):
    r = gh.client.post("/github/device/start", headers=DEV, json={})
    assert r.status_code == 404 and "token" in r.get_json()["error"]
    r = gh.client.post("/github/device/poll", headers=DEV, json={})
    assert r.status_code == 404 and r.get_json()["error"].startswith("There is no sign-in")
    assert gh.calls == []


def test_the_device_sign_in_with_a_client_id(gh, caplog):
    from puffpai.config import get_config
    caplog.set_level("DEBUG")
    path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"github_oauth_client_id": "Ov23liAbCdEfGh123456"}, fh)
    token = _token("gho_")
    gh.token = token
    gh.device_answers = [{"error": "authorization_pending"},
                         {"access_token": token, "token_type": "bearer", "scope": ""}]
    started = gh.client.post("/github/device/start", headers=DEV, json={}).get_json()
    assert started["user_code"] == "WDJB-MJHT"
    assert started["verification_uri"] == "https://github.com/login/device"
    assert "dc_" not in json.dumps(started)               # the device code stays here
    posted = [c for c in gh.calls if c["method"] == "POST"]
    assert posted[0]["data"] == {"client_id": "Ov23liAbCdEfGh123456", "scope": ""}
    assert not any("secret" in json.dumps(c["data"]) for c in posted)
    assert gh.client.post("/github/device/poll", headers=DEV_B, json={}).status_code == 403
    github_account._pending["next_at"] = 0               # noqa: SLF001 - no waiting
    first = gh.client.post("/github/device/poll", headers=DEV, json={}).get_json()
    assert first["pending"] is True
    github_account._pending["next_at"] = 0               # noqa: SLF001
    done = gh.client.post("/github/device/poll", headers=DEV, json={}).get_json()
    assert done["connected"] is True and done["method"] == "device"
    assert token not in json.dumps(done) and token not in caplog.text
    assert grep_tree(_state(), token) == []
    out = gh.client.post("/github/disconnect", headers=DEV, json={}).get_json()
    assert out["revoke_url"] == "https://github.com/settings/applications"


# -- her projects in the build -------------------------------------------------------

JOBS = [{"id": "plan", "name": "Garden planner", "does": "plan my garden beds",
         "subject": ["garden"]}]


def test_her_projects_become_finds_and_private_ones_only_when_readable(gh):
    _connect(gh, private=False)
    got = github_account.candidates_for(JOBS)
    refs = {c["ref"] for c in got}
    assert f"github:{LOGIN}/garden-planner" in refs and f"github:{LOGIN}/seed-catalog" in refs
    assert f"github:{LOGIN}/secret-garden" not in refs
    assert all(c["origin"] == "From your GitHub" and c["from_your_github"] is True
               for c in got)
    from puffpai.build import pipeline
    assert pipeline.part_of(got[0])["from_your_github"] is True
    github_account._drop("repos.json")                    # noqa: SLF001 - read again
    gh.client.post("/github/disconnect", headers=DEV, json={})
    _connect(gh, private=True)
    mine = {c["ref"]: c for c in github_account.candidates_for(JOBS)}
    assert mine[f"github:{LOGIN}/secret-garden"]["private"] is True
    assert github_account.candidates_for([{"id": "x", "name": "Weather", "does": "rain"}]) == []


def test_build_find_lists_hers_first(gh, monkeypatch):
    from puffpai.build import find as build_find
    _connect(gh)
    monkeypatch.setattr(build_find, "search_jobs_for", lambda jobs, goal: [
        {"ref": f"github:{LOGIN}/garden-planner", "name": "garden-planner",
         "type": "browser_app", "source": "github"}])
    monkeypatch.setattr(build_find, "live_for", lambda jobs: [])
    got = {c["ref"]: c for c in build_find.find(JOBS, "garden", read_skills=False)}
    assert got[f"github:{LOGIN}/garden-planner"]["origin"] == "From your GitHub"


def test_a_private_project_needs_its_own_setup_row(gh):
    from puffpai.build import make as make_mod
    from puffpai.build import setup as setup_mod
    part = {"ref": f"github:{LOGIN}/secret-garden", "name": "secret-garden",
            "type": "browser_app", "class": "APP", "origin": "From your GitHub",
            "private": True, "full_name": f"{LOGIN}/secret-garden"}
    job = {"id": "plan", "name": "Garden planner", "route": "A",
           "pick": {"ref": part["ref"], "class": "APP"}, "ingredients": [part["ref"]]}
    rows = setup_mod.rows_for([job], {part["ref"]: part})
    row = next(r for r in rows if r.get("make") == setup_mod.MAKE_PRIVATE)
    assert row["label"] == "Use your private project secret-garden"
    pulled = []
    hooks = {"pull_private": lambda c: pulled.append(c["ref"]) or {"ok": False,
                                                                    "reason": "stub"}}
    no_row = make_mod._make_app(job, {"jobs": [job], "setup": []}, [part], hooks,  # noqa: SLF001
                                lambda _l: None)
    assert not no_row["ok"] and "not on your setup list" in no_row["reason"]
    assert pulled == []
    with_row = make_mod._make_app(job, {"jobs": [job], "setup": rows}, [part], hooks,  # noqa: SLF001
                                  lambda _l: None)
    assert pulled == [part["ref"]] and "stub" in with_row["reason"]


def test_a_private_project_is_never_downloaded_before_the_yes(gh):
    from puffpai.build import check as check_mod
    _connect(gh, private=True)
    part = {"ref": f"github:{LOGIN}/secret-garden", "name": "secret-garden",
            "type": "browser_app", "class": "APP", "origin": "From your GitHub",
            "private": True, "full_name": f"{LOGIN}/secret-garden"}
    before = len(gh.calls)
    got = check_mod.check(part, prepare=lambda *a: pytest.fail("downloaded before the yes"))
    assert got["ok"] and got.get("private") is True and len(gh.calls) == before
    item = github_account.register_found(part)
    from puffpai.wizard import find_parts
    found = find_parts.found_for(item)
    with pytest.raises(github_account.GitHubError) as exc:
        github_account.private_archive(found)
    assert exc.value.code == "not_yes"


def test_pulling_a_private_project_keeps_the_token_off_disk(gh, caplog):
    from puffpai.integrate import fetch, records
    from puffpai.wizard import find_parts
    caplog.set_level("DEBUG")
    token = _connect(gh, private=True)
    part = {"ref": f"github:{LOGIN}/secret-garden", "name": "secret-garden",
            "origin": "From your GitHub", "private": True,
            "full_name": f"{LOGIN}/secret-garden"}
    found = find_parts.found_for(github_account.register_found(part))
    assert found["repo"]["private"] is True and found["repo"]["pinned_commit"] == SHA
    iid = records.new_id("secret-garden")
    with github_account.private_pull(part["full_name"]):
        got = fetch.fetch(iid, found)
    assert got["commit"] == SHA and got["final_host"] == "codeload.github.com"
    assert token not in got["url"] and TEMP not in json.dumps(got)
    tarball = [c for c in gh.calls if "/tarball/" in c["url"]]
    codeload = [c for c in gh.calls if "codeload" in c["url"]]
    assert _auth_of(tarball[0]) == f"Bearer {token}"
    assert codeload and _auth_of(codeload[0]) == ""       # never on the redirect
    for root in (_state(), str(gh.tmp / "apps")):
        assert grep_tree(root, token) == [] and grep_tree(root, TEMP) == []
    assert token not in caplog.text and TEMP not in caplog.text


def test_an_assembled_app_may_not_use_her_github_token(gh):
    from puffpai.assemble.server import host
    spec = {"settings": [{"key": "gh", "kind": "key", "service": "github"}]}
    with pytest.raises(host.AssembleError):
        host._key_service(spec, "gh")                      # noqa: SLF001
    spec = {"settings": [{"key": "b", "kind": "key", "service": "brave_search"}]}
    assert host._key_service(spec, "b") == "brave_search"  # noqa: SLF001


# -- every class, and her own projects without a licence ---------------

def test_her_projects_of_every_class_and_her_own_marked(gh):
    gh.mine.append(_repo("garden-math", language="Python", licence=None,
                         description="garden area library", topics=["library"]))
    gh.mine.append(_repo("secret-garden-lib", private=True, language="Python",
                         description="garden library", topics=["library"]))
    gh.starred.append(_repo("garden-tracker", owner="other-maker", language="Go",
                            licence=None, description="garden cli", topics=["cli"]))
    _connect(gh, private=True)
    got = {c["ref"]: c for c in github_account.candidates_for(JOBS)}
    lib = got[f"github:{LOGIN}/garden-math"]
    assert lib["type"] == "library" and lib["own_project"] is True
    assert lib["origin_words"] == "Your own project"
    other = got["github:other-maker/garden-tracker"]
    assert other["type"] == "cli" and other["own_project"] is False
    assert "origin_words" not in other
    assert f"github:{LOGIN}/secret-garden-lib" not in got   # private: apps only
    assert f"github:{LOGIN}/secret-garden" in got
    from puffpai.build import classify
    assert classify.classify(dict(lib)) in ("DATA", "TOOL", "GUIDE")
    assert len(got) <= github_account.MINE_PER_BUILD == 12
    from puffpai.finding import find as finding_find
    assert finding_find.recall(f"github:{LOGIN}/garden-math") is not None
    assert finding_find.recall(f"github:{LOGIN}/secret-garden") is None    # never kept


def test_only_her_own_project_skips_the_licence_rule(gh):
    from puffpai.integrate import apply
    from puffpai.wizard import find_parts
    gh.mine.append(_repo("garden-math", licence=None, description="garden library"))
    gh.starred.append(_repo("garden-tracker", owner="other-maker", licence=None,
                            description="garden app"))
    _connect(gh)
    mine = find_parts.found_for(github_account.register_found(
        {"ref": "x", "origin": "From your GitHub", "full_name": f"{LOGIN}/garden-math"}))
    theirs = find_parts.found_for(github_account.register_found(
        {"ref": "y", "origin": "From your GitHub", "full_name": "other-maker/garden-tracker"}))
    assert mine["own_project"] is True and theirs["own_project"] is False
    assert apply._own_project(mine) is True and apply._own_project(theirs) is False
    forged = dict(theirs, own_project=True)
    assert apply._own_project(forged) is False          # checked again, from the owner
    base = {"source": {"host": "github.com"}, "fetch": {}, "plan": {}, "license": {"read": {}}}
    codes = {r["code"]: r["result"] for r in apply.code_project_rows(base)}
    assert codes["no_license"] == "fail"
    codes = {r["code"]: r["result"] for r in
             apply.code_project_rows(dict(base, own_project=True))}
    assert "no_license" not in codes and codes["own_project"] == "pass"
    assert "too_big" in codes                            # the code checks still run
    gh.client.post("/github/disconnect", headers=DEV, json={})
    assert apply._own_project(mine) is False            # not connected: no waiver


# -- more checks ----------------------------------------------------

def test_disconnect_while_locked_is_cleared_the_moment_the_vault_opens(gh):
    from keys_support import PIN
    token = _connect(gh)
    gh.vault.lock()
    body = gh.client.post("/github/disconnect", headers=DEV, json={}).get_json()
    assert "the moment you next open it" in body["line"]
    gh.vault.unlock(PIN)                       # nothing else is called
    assert keys._record_for(gh.vault, "github") is None   # noqa: SLF001
    assert not os.path.exists(os.path.join(_state(), "keys_remove_pending.json"))
    assert grep_tree(_state(), token) == []


def test_a_key_added_again_after_a_locked_removal_survives_the_unlock(gh):
    from keys_support import PIN
    _connect(gh)
    gh.vault.lock()
    gh.client.post("/github/disconnect", headers=DEV, json={})
    gh.vault.unlock(PIN)
    token = _connect(gh)                       # connected again: kept
    gh.vault.lock()
    gh.vault.unlock(PIN)
    assert keys._record_for(gh.vault, "github")["value"] == token   # noqa: SLF001


def test_the_status_shows_the_search_limit(gh):
    _connect(gh)
    st = github_account.status()
    assert st["rate"] == {"limit": 30, "remaining": 29, "reset": 1999999999}
    assert st["rate_window"] == "minute"


def test_no_vault_yet_is_said_plainly(gh, monkeypatch, tmp_path):
    from puffpai.security import vault as vault_mod
    empty = vault_mod.Vault(vault_dir=str(tmp_path / "novault"))
    keys.set_vault_provider(lambda: empty)
    monkeypatch.setattr(api_server, "_vault_instance", empty)
    st = gh.client.get("/github/status", headers=DEV).get_json()
    assert st["vault_set_up"] is False and st["vault_locked"] is False
    assert st["line"] == "Not connected. No vault yet."
    r = gh.client.post("/github/token", headers=DEV, json={"token": _token()})
    assert r.status_code == 409 and r.get_json()["code"] == "no_vault"
    assert gh.calls == []


def _no_setup_yet():
    from puffpai.config import get_config
    path = os.path.join(get_config().CONFIG_DIR, "build_manifest.json")
    if os.path.exists(path):
        os.unlink(path)
    egress_doors.reset_cache()


def _wizard_under_way(monkeypatch, on: bool = True):
    from puffpai.wizard import service
    monkeypatch.setattr(service, "setup_in_progress", lambda *a, **k: on)


def _keyed(gh, start):
    return [(urlsplit(c["url"]).hostname, urlsplit(c["url"]).path)
            for c in gh.calls[start:] if _auth_of(c)]


def test_connecting_before_the_bot_is_set_up_sends_the_token_once(gh, caplog, monkeypatch):
    from puffpai.finding import sites
    from puffpai.security import egress_ledger
    caplog.set_level("DEBUG")
    _no_setup_yet()
    _wizard_under_way(monkeypatch)
    assert github_account.before_setup() is True
    token = _connect(gh)
    assert _keyed(gh, 0) == [("api.github.com", "/user")]       # exactly one request
    st = github_account.status()
    assert st["connected"] and st["login"] == LOGIN and st["name"] == "Octo Owner"
    assert st["switched_on"] is False                          # waits for the run setting
    lines = [l for l in egress_ledger.read(1, limit=200) if l.get("host") == "api.github.com"]
    assert lines and all(l["service"] in ("github", "code_search") for l in lines)
    n = len(gh.calls)
    sites.github("garden before")
    github_account.refresh_repos()
    github_account.refresh_rate()
    github_account.candidates_for(JOBS)
    assert _keyed(gh, n) == []                                # nothing else keyed
    set_posture("enumerated")                                  # the plan allows it
    assert github_account.switched_on() is True
    sites.github("garden after")
    assert _auth_of(_searches(gh)[-1]) == f"Bearer {token}"
    assert grep_tree(_state(), token) == [] and token not in caplog.text


def test_without_a_wizard_run_under_way_there_is_no_pre_setup_check(gh, monkeypatch):
    _no_setup_yet()
    _wizard_under_way(monkeypatch, on=False)
    gh.token = _token()
    r = gh.client.post("/github/token", headers=DEV, json={"token": gh.token})
    assert r.status_code == 409 and gh.calls == []
    assert github_account.connected() is False
    assert outside_switches.get("github") is not True        # no yes was kept


@pytest.mark.parametrize("damage", ["delete", "{not json", "", "[]", '{"schema": "x"}'])
def test_a_damaged_or_missing_manifest_after_setup_never_sends_the_token(gh, monkeypatch,
                                                                         damage):
    """Ported from a probe of the setup path."""
    from puffpai.config import get_config
    from puffpai.finding import sites
    _no_setup_yet()
    _wizard_under_way(monkeypatch)
    _connect(gh, private=True)
    set_posture("island")                                      # saved offline
    assert github_account.switched_on() is False
    _wizard_under_way(monkeypatch, on=False)                   # the run is finished
    path = os.path.join(get_config().CONFIG_DIR, "build_manifest.json")
    if damage == "delete":
        os.unlink(path)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(damage)
    egress_doors.reset_cache()
    n = len(gh.calls)
    sites.github("garden damaged")
    github_account.refresh_repos()
    github_account.refresh_profile()
    github_account.refresh_rate()
    github_account.candidates_for(JOBS)
    assert github_account.before_setup() is False
    assert _keyed(gh, n) == []


def test_once_a_run_setting_was_seen_a_lost_manifest_is_never_pre_setup(gh, monkeypatch):
    from puffpai.config import get_config
    set_posture("island")
    _wizard_under_way(monkeypatch)                             # even a run under way
    assert github_account.before_setup() is False              # notes it was seen
    os.unlink(os.path.join(get_config().CONFIG_DIR, "build_manifest.json"))
    egress_doors.reset_cache()
    assert github_account.before_setup() is False


def test_the_wizard_signal_fails_closed():
    from puffpai.wizard import service
    assert service.setup_in_progress() is False                # no run record here


def test_before_setup_a_switched_off_github_stays_off(gh, monkeypatch):
    from puffpai import outside_services
    _no_setup_yet()
    _wizard_under_way(monkeypatch)
    _connect(gh)
    outside_services.switch_off("github", by="owner")
    assert github_account.switched_on() is False and github_account.search_key_id() is None


def test_the_setup_check_is_only_for_the_key_broker(gh):
    with pytest.raises(PermissionError):
        egress_doors.setup_check_hop("github", _token())
