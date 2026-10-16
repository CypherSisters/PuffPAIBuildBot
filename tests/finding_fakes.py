"""Recorded site answers for the find step's tests (no network).

The answers in ``tests/fixtures/finding`` were recorded from the real sites on
2026-10-04 and trimmed to the fields BuildBot reads. :class:`Sites` stands in
for ``puffpai.finding.net._send`` and answers from them.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import io
import urllib.parse
import zipfile
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures" / "finding"
COMMIT = (FIX / "gh_commit__text-to-cad.txt").read_text(encoding="ascii").strip()


def _slug(q: str) -> str:
    return "_".join(q.split())


class Sites:
    """``_send`` with recorded answers. ``calls`` keeps (service, url)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.extra: dict[str, tuple[int, bytes]] = {}

    def _file(self, name: str, status: int = 200) -> dict:
        p = FIX / name
        if not p.exists():
            return {"status": 404, "body": b"{}", "reason": "ok", "headers": {}}
        return {"status": status, "body": p.read_bytes(), "reason": "ok", "headers": {}}

    def __call__(self, url, *, hosts, service, max_bytes, accept=None):
        self.calls.append((service, url))
        if url in self.extra:
            status, body = self.extra[url]
            return {"status": status, "body": body, "reason": "ok", "headers": {}}
        u = urllib.parse.urlsplit(url)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        host, path = u.hostname, u.path
        assert host in set(hosts), (host, hosts)
        if host == "clawhub.ai":
            if path == "/api/v1/search":
                return self._file(f"clawhub__{_slug(qs['q'])}.json")
            if path.endswith("/verify"):
                slug = path.split("/")[4]
                return self._file(f"verify__{qs.get('ownerHandle', '')}_{slug}.json")
            if path == "/api/v1/download":
                return self._file(f"download__{qs['slug']}.zip")
            if path.endswith("/file"):
                slug = path.split("/")[4]
                p = FIX / f"download__{slug}.zip"
                if not p.exists():
                    return {"status": 404, "body": b"", "reason": "ok", "headers": {}}
                body = zipfile.ZipFile(io.BytesIO(p.read_bytes())).read(qs["path"])
                return {"status": 200, "body": body, "reason": "ok", "headers": {}}
        if host == "skills.sh":
            return self._file(f"skillssh__{_slug(qs['q'])}.json")
        if host == "api.github.com":
            if path == "/search/repositories":
                return self._file(f"github__{_slug(qs['q'])}.json")
            if path == "/repos/earthtojake/text-to-cad/commits/HEAD":
                return self._file("gh_commit__text-to-cad.txt")
            if path.startswith("/repos/earthtojake/text-to-cad/git/trees/"):
                return self._file("gh_tree__text-to-cad.json")
        if host == "raw.githubusercontent.com":
            if path.endswith("/skills/bambu-labs/SKILL.md"):
                return self._file("raw__bambu-labs_SKILL.md")
            if path.startswith(f"/earthtojake/text-to-cad/{COMMIT}/"):
                return {"status": 200, "body": b"recorded stand-in file\n", "reason": "ok",
                        "headers": {}}
        if host == "codeberg.org":
            return self._file(f"codeberg__{_slug(qs['q'])}.json")
        if host == "registry.npmjs.org":
            return self._file(f"npm__{_slug(qs['text'])}.json")
        if host == "pypi.org":
            name = path.split("/")[2].lower()
            return self._file(f"pypi__{name}.json")
        if host == "registry.modelcontextprotocol.io":
            p = FIX / f"mcpw__{qs['search']}.json"
            if not p.exists():
                return {"status": 200, "body": b'{"servers":[]}', "reason": "ok",
                        "headers": {}}
            return self._file(p.name)
        if host == "nousresearch.github.io":
            return self._file("hermes_index_small.json")
        return {"status": 404, "body": b"", "reason": "ok", "headers": {}}

    def hosts_asked(self) -> set[str]:
        return {urllib.parse.urlsplit(u).hostname for _s, u in self.calls}

    def services_of(self, host: str) -> set[str]:
        return {s for s, u in self.calls if urllib.parse.urlsplit(u).hostname == host}
