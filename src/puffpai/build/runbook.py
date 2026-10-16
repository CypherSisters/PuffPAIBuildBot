"""Toolsmith's run book: what a person who installs apps every day knows
before reading a single file.

* :data:`RUN_TABLE`, one row per kind of project (its stack), read from the
  downloaded files (:func:`detect`): the exact start command on the
  assigned port and 127.0.0.1, given to the model first (:func:`facts_text`).
* The address an app's page was told for its own API (``*_PUBLIC_URL``,
  ``*_BASE_URL``, ``VITE_*``, a served ``config.js``): found
  (:func:`api_settings`, :func:`served_configs`) and set to the real one
  (:func:`api_override`, :func:`fix_env_text`).
* Compose on Windows: ``${PWD}``, port variables, a busy host port
  (:func:`prepare_compose`) and the example files its README says to copy
  (:func:`example_copies`).
* The working check: README demo credentials (:func:`readme_credentials`),
  an error banner on the page (:func:`banner`) and the app's own request
  lines in its log (:func:`request_counts`).

Goal-agnostic: no app is named here, only kinds of projects.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

# ── the run table ──────────────────────────────────────────────────────────

#: One row per stack, first match wins. ``when`` names a test in _WHEN;
#: commands use {P} (the assigned port), {pm} (the package runner), {dir}
#: (the built folder), {expose} (a Dockerfile's port), {name} (its image).
#: ``via``: start_server (a server BuildBot keeps) or terminal (it ends).
RUN_TABLE: list[dict] = [
    {"stack": "docker compose", "when": "compose",
     "start": "docker compose up -d", "via": "terminal",
     "why": "its own compose file (BuildBot runs a local copy on 127.0.0.1)"},
    {"stack": "client-only app, built then served as static files", "when": "static_build",
     "install": "{pm} install", "build": "{pm} run build",
     "start": "python -I -m http.server {P} --bind 127.0.0.1 -d {dir}", "via": "start_server",
     "why": "build once, then serve the built folder; no long-running dev server"},
    {"stack": "Next.js", "when": "next",
     "install": "{pm} install", "start": "npx next dev -p {P} -H 127.0.0.1",
     "via": "start_server",
     "fallback": "{pm} run build && npx next start -p {P} -H 127.0.0.1"},
    {"stack": "Vite", "when": "vite",
     "install": "{pm} install", "start": "npx vite --port {P} --host 127.0.0.1 --strictPort",
     "via": "start_server"},
    {"stack": "Create React App", "when": "cra",
     "install": "{pm} install", "start": "PORT={P} HOST=127.0.0.1 npm start",
     "via": "start_server"},
    {"stack": "Dockerfile", "when": "dockerfile",
     "build": "docker build -t {name} .",
     "start": "docker run -d -p 127.0.0.1:{P}:{expose} {name}", "via": "terminal"},
    {"stack": "Node app with its own start script", "when": "node_script",
     "install": "{pm} install", "start": "PORT={P} HOST=127.0.0.1 {pm} run {script}",
     "via": "start_server",
     "why": "its package.json says how it starts (a server of its own), not static files"},
    {"stack": "plain web page (index.html, no build)", "when": "plain_html",
     "start": "python -I -m http.server {P} --bind 127.0.0.1", "via": "start_server"},
    {"stack": "Python web app", "when": "python_web",
     "install": "python -m venv .venv, then its requirements into .venv",
     "start": "{readme_cmd}", "via": "start_server"},
]

#: Built folders a client-only app's build writes (first found in its scripts).
BUILT_DIRS = ("dist", "build", "out")
#: Packages that mean the app has a server of its own (not client-only).
_SERVER_DEPS = frozenset("""express fastify koa hapi @hapi/hapi next nuxt @sveltejs/kit
@remix-run/node @remix-run/serve hono @nestjs/core socket.io ws pg mysql2 mongoose
sqlite3 better-sqlite3 prisma @prisma/client astro vite-plugin-ssr
""".split())
_PY_FRAMEWORKS = (("django", "Django"), ("fastapi", "FastAPI"), ("flask", "Flask"))


def _read(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _package(folder: str) -> dict:
    try:
        got = json.loads(_read(os.path.join(folder, "package.json")) or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def _deps(pkg: dict) -> set[str]:
    out: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            out |= set(pkg[key])
    return out


def package_runner(folder: str, pkg: Optional[dict] = None) -> str:
    """How to call its package manager without a global install or
    ``corepack enable`` (needs admin on Windows): ``npx -y pnpm@9.1.0``
    from a packageManager field, a lock file's manager, else npm."""
    pkg = _package(folder) if pkg is None else pkg
    pm = str(pkg.get("packageManager") or "")
    m = re.match(r"^(pnpm|yarn|npm|bun)@([\w.\-]+)", pm)
    if m:
        return "npm" if m.group(1) == "npm" else f"npx -y {m.group(1)}@{m.group(2)}"
    if os.path.isfile(os.path.join(folder, "pnpm-lock.yaml")):
        return "npx -y pnpm"
    if os.path.isfile(os.path.join(folder, "yarn.lock")):
        return "npx -y yarn"
    return "npm"


def built_dir(pkg: dict) -> str:
    """The folder its build script writes (dist, build, out), or ""."""
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    build = str(scripts.get("build") or "")
    if not build:
        return ""
    m = re.search(r"(?:--out-?dir|--outDir|-o)[ =]\.?/?(\w[\w-]*)", build)
    if m:
        return m.group(1)
    deps = _deps(pkg)
    if "react-scripts" in deps:
        return "build"
    if "next" in deps and "export" in build:
        return "out"
    if "vite" in deps or "vite" in build:
        return "dist"
    for d in BUILT_DIRS:
        # (a folder named in the script, not the word "build" after a tool)
        if re.search(rf"(?:[=/]|\s-\w\s|^)\.?/?{d}(?![\w-])", build):
            return d
    if re.search(r"\b(?:webpack|parcel|rollup|esbuild)\b", build):
        return "dist"
    return ""


#: The scripts that start an app, in the order one is picked.
_START_SCRIPTS = ("start", "dev", "serve")
#: A script that runs a server file of its own (``node server.js``,
#: ``nodemon app.js``, ``ts-node src/index.ts``), not a client tool's dev server.
_NODE_SERVER_RE = re.compile(r"\b(?:node|nodemon|ts-node|ts-node-dev|tsx|bun|deno\s+run)"
                             r"\s+(?!.*\b(?:vite|webpack|serve)\b)\S+")
#: A script that only builds, checks or prints (never a server).
_BUILD_ONLY_RE = re.compile(
    r"^\s*(?:npx\s+)?(?:tsc|rollup|esbuild|sass|tailwindcss|eslint|prettier|jest|vitest|"
    r"mocha|echo|rimraf|webpack(?!\s+serve|-dev-server)|parcel\s+build|vite\s+build|"
    r"next\s+build|react-scripts\s+build)\b(?!.*(?:&&|;|\|\|).*\b(?:node|serve|start|dev)\b)")


def _scripts(pkg: dict) -> dict:
    s = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    return {k: str(v) for k, v in s.items() if isinstance(k, str) and isinstance(v, str)}


def server_script(pkg: dict) -> str:
    """The name of its start, dev or serve script when one is there and is
    not only a build (it starts the app), else ""."""
    scripts = _scripts(pkg)
    for name in _START_SCRIPTS:
        cmd = scripts.get(name, "").strip()
        if cmd and not _BUILD_ONLY_RE.match(cmd):
            return name
    return ""


def _client_only(folder: str, pkg: dict) -> bool:
    deps = _deps(pkg)
    if deps & _SERVER_DEPS:
        return False
    scripts = _scripts(pkg)
    return not any(_NODE_SERVER_RE.search(scripts.get(n, "")) for n in _START_SCRIPTS)


def _has_page(folder: str, pkg: dict) -> bool:
    """An index.html, or a build output: something BuildBot could serve as
    static files when nothing better says how it starts."""
    return os.path.isfile(os.path.join(folder, "index.html")) or bool(built_dir(pkg))


def _dockerfile_port(folder: str) -> str:
    m = re.search(r"(?im)^\s*EXPOSE\s+(\d{2,5})", _read(os.path.join(folder, "Dockerfile")))
    return m.group(1) if m else "80"


_PY_RUN = re.compile(r"(?im)^\s*(?:[$>]\s*)?((?:python3?\s+(?:-m\s+)?)?(?:flask\b[^\n`|;&]*?\brun\b|uvicorn\s+\S+|"
                     r"\S*manage\.py\s+runserver|gunicorn\s+\S+(?:\s+\S+)*?)[^\n`]*)")


def with_port(cmd: str, port: int) -> str:
    """A Python server command with 127.0.0.1 and *port* put in."""
    cmd = cmd.strip()
    if re.search(r"\brunserver\b", cmd):
        return re.sub(r"\brunserver\b(\s+\S*:?\d+)?", f"runserver 127.0.0.1:{port}", cmd, count=1)
    if re.search(r"\bgunicorn\b", cmd):
        cmd = re.sub(r"\s(?:-b|--bind)[ =]\S+", "", cmd)
        return re.sub(r"\bgunicorn\b", f"gunicorn -b 127.0.0.1:{port}", cmd, count=1)
    cmd = re.sub(r"\s(?:--host|-h)[ =]\S+|\s(?:--port|-p)[ =]\S+", "", cmd)
    return f"{cmd} --host 127.0.0.1 --port {port}"


def _python_web(folder: str, readme: str) -> tuple[str, str]:
    """``(framework, README command with the port)`` or ("", "")."""
    text = " ".join(_read(os.path.join(folder, n), 50_000).lower() for n in
                    ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"))
    fw = next((label for key, label in _PY_FRAMEWORKS if re.search(rf"\b{key}\b", text)), "")
    if not fw:
        return "", ""
    m = _PY_RUN.search(readme or "")
    if m:
        return fw, m.group(1).strip()
    if fw == "Django" and os.path.isfile(os.path.join(folder, "manage.py")):
        return fw, "python manage.py runserver"
    if fw == "Flask":
        return fw, "python -m flask run"
    return fw, ""


def _compose_file(folder: str) -> str:
    return next((n for n in ("compose.yaml", "compose.yml", "docker-compose.yml",
                             "docker-compose.yaml") if os.path.isfile(os.path.join(folder, n))),
                "")


_WHEN: dict[str, Callable[[str, dict, str], bool]] = {
    "compose": lambda f, pkg, r: bool(_compose_file(f)),
    "static_build": lambda f, pkg, r: bool(pkg) and bool(built_dir(pkg))
    and _client_only(f, pkg) and "next" not in _deps(pkg),
    "next": lambda f, pkg, r: "next" in _deps(pkg),
    "vite": lambda f, pkg, r: "vite" in _deps(pkg),
    "cra": lambda f, pkg, r: "react-scripts" in _deps(pkg),
    "dockerfile": lambda f, pkg, r: os.path.isfile(os.path.join(f, "Dockerfile")) and not pkg,
    # A start, dev or serve script beats serving its files: it says how it starts.
    "node_script": lambda f, pkg, r: bool(server_script(pkg)) and _has_page(f, pkg),
    "plain_html": lambda f, pkg, r: os.path.isfile(os.path.join(f, "index.html"))
    and not (pkg.get("scripts") or {}).get("build") and not server_script(pkg),
    "python_web": lambda f, pkg, r: bool(_python_web(f, r)[0]),
}


def detect(folder: str, port: int, readme: str = "", name: str = "app") -> dict:
    """The run-table row for the project in *folder*, filled in:
    ``{"stack", "install"?, "build"?, "start", "via", "fallback"?}``; {}
    when no row fits (the model reads its README as before)."""
    pkg = _package(folder)
    for row in RUN_TABLE:
        if not _WHEN[row["when"]](folder, pkg, readme):
            continue
        values = {"P": port, "pm": package_runner(folder, pkg), "dir": built_dir(pkg),
                  "script": server_script(pkg) or "start",
                  "expose": _dockerfile_port(folder),
                  "name": re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-") or "app"}
        out = {"stack": row["stack"], "via": row["via"]}
        if row["when"] == "python_web":
            fw, cmd = _python_web(folder, readme)
            if not cmd:
                continue
            out["stack"] = f"Python ({fw})"
            values["readme_cmd"] = with_port(cmd, port)
        for key in ("install", "build", "start", "fallback"):
            if row.get(key):
                out[key] = row[key].format(**values)
        if row["when"] == "static_build":
            # Its dev server, when the build fails.
            dev = next((r for r in RUN_TABLE if r["when"] in ("vite", "cra")
                        and _WHEN[r["when"]](folder, pkg, readme)), None)
            if dev:
                out["fallback"] = dev["start"].format(**values)
        return out
    return {}


def facts_text(*, run: dict, port: int, creds: Optional[tuple[str, str]] = None,
               api: Any = (), configs: Any = ()) -> str:
    """The short Facts block the model reads first, and what to do with it."""
    lines = ["Facts (read from its files before you start):"]
    if run:
        lines.append(f"- Stack: {run['stack']}.")
        steps = [run[k] for k in ("install", "build") if run.get(k)]
        if steps:
            lines.append("- First (terminal): " + "; then ".join(steps) + ".")
        lines.append(f"- Start ({run['via']}): {run['start']}")
        if run.get("fallback"):
            lines.append(f"- If that fails: {run['fallback']}")
    else:
        lines.append("- Stack: not recognised; follow its README.")
    lines.append(f"- Assigned port: {port} on 127.0.0.1.")
    if creds:
        lines.append(f"- Its README's sign-in: {creds[0]} / {creds[1]}.")
    told = [f"{a['name']}={a['value']} ({a['where']})" for a in api][:6]
    told += [f"{c['path']} says {c['line']}" for c in configs][:3]
    if told:
        lines.append("- Its page is told its API address in: " + "; ".join(told)
                     + f". If the port changes, set it to http://127.0.0.1:<the real port>.")
    if run:
        lines.append("Start it with the command above unless it fails; read files only to "
                     "fix a failure.")
    return "\n".join(lines)


# ── the address its page is told ───────────────────────────────────────────

API_NAME = re.compile(r"^(?:\w*_PUBLIC_URL|\w*_BASE_URL|API_ROOT|PUBLIC_URL|BASE_URL|"
                      r"NEXT_PUBLIC_\w+|VITE_\w+)$")
_LOCAL_URL = re.compile(r"(https?://)(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})")
ENV_NAMES = (".env", ".env.example", ".env.sample", ".env.dist", ".env.template",
             "example.env", "sample.env")
_ENV_LINE = re.compile(r"(?m)^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*[\"']?([^\"'#\n]*)")


def env_values(text: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in _ENV_LINE.finditer(text or "")}


def _local_port_of(value: str) -> int:
    m = _LOCAL_URL.search(value or "")
    return int(m.group(2)) if m else 0


def api_settings(folder: str) -> list[dict]:
    """``[{"name", "value", "where", "port"}]``: settings named like an API
    address (``*_PUBLIC_URL``, ``VITE_*``...) whose value is a local address
    with a port, in its env files and compose files (top folder)."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    names = list(ENV_NAMES) + [n for n in ("compose.yaml", "compose.yml", "docker-compose.yml",
                                           "docker-compose.yaml")]
    for n in names:
        text = _read(os.path.join(folder, n))
        if not text:
            continue
        pairs = env_values(text) if n in ENV_NAMES else _compose_env(text)
        for key, value in pairs.items():
            value = _default_of(value)
            port = _local_port_of(value)
            if API_NAME.match(key) and port and (key, n) not in seen:
                seen.add((key, n))
                out.append({"name": key, "value": value, "where": n, "port": port})
    return out


def _default_of(value: str) -> str:
    """``${NAME:-http://localhost:8080}`` -> its default; anything else as is."""
    m = re.fullmatch(r"\s*\$\{\w+:?-([^}]*)\}\s*", str(value or ""))
    return m.group(1) if m else str(value or "")


def _compose_env(text: str) -> dict:
    """Every ``KEY=value`` / ``KEY: value`` under an ``environment:`` block."""
    return {k: v for _svc, k, v in _service_env(text) if k}


def _service_env(text: str) -> list[tuple[str, str, str]]:
    """``[(service, key, value)]`` from the services' ``environment:``
    blocks; a service's ``env_file`` shows as ``(service, "", "env_file")``."""
    out: list[tuple[str, str, str]] = []
    service, svc_indent, env_at = "", -1, None
    in_services = False
    for line in (text or "").splitlines():
        body = line.rstrip()
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(body) - len(body.lstrip(" "))
        if indent == 0:
            in_services, service, svc_indent, env_at = stripped.startswith("services:"), "", -1, None
            continue
        if not in_services:
            continue
        if svc_indent == -1:
            svc_indent = indent
        if indent <= svc_indent:
            service, env_at = stripped.split(":", 1)[0].strip().strip("\"'"), None
            continue
        if env_at is not None and indent <= env_at:
            env_at = None
        if env_at is None:
            if re.match(r"^environment\s*:\s*$", stripped):
                env_at = indent
            elif re.match(r"^env_file\s*:", stripped):
                out.append((service, "", "env_file"))
            continue
        m = re.match(r"^-\s*[\"']?([A-Za-z_]\w*)=([^\"'\n]*)", stripped) or \
            re.match(r"^[\"']?([A-Za-z_]\w*)[\"']?\s*:\s*[\"']?([^\"'#\n]*)", stripped)
        if m and service:
            out.append((service, m.group(1), m.group(2).strip()))
    return out


def served_configs(folder: str) -> list[dict]:
    """``[{"path", "line"}]``: config.js / env.js files its page loads that
    name a local address with a port (public/, static/, the top folder...)."""
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        depth = os.path.relpath(dirpath, folder).count(os.sep)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
        if depth >= 2:
            dirnames[:] = []
        for n in filenames:
            if not re.fullmatch(r"(?:config|env|env-config|runtime-config|app-config|settings)"
                                r"\.js", n, re.I):
                continue
            path = os.path.join(dirpath, n)
            for line in _read(path, 20_000).splitlines():
                if _LOCAL_URL.search(line):
                    out.append({"path": os.path.relpath(path, folder).replace(os.sep, "/"),
                                "line": line.strip()[:160]})
                    break
    return out


def real_port(told: int, published: list[tuple[int, int]], remapped: dict) -> int:
    """The host port a page told *told* really reaches now: the host port
    published for that container port, or what a remapped host port became;
    0 when it is right already or not known."""
    if told in remapped:
        return remapped[told]
    for host, container in published:
        if container == told and host != told:
            return host
    return 0


def fix_env_text(text: str, published: list[tuple[int, int]], remapped: dict) -> str:
    """An env file's text with each API-address setting set to its real
    ``http://127.0.0.1:<port>`` (everything else as it was)."""
    def one(m: Any) -> str:
        key, value = m.group(1), m.group(2)
        port = _local_port_of(value)
        new = real_port(port, published, remapped) if API_NAME.match(key) and port else 0
        if not new:
            return m.group(0)
        return m.group(0).replace(_LOCAL_URL.search(value).group(0), f"http://127.0.0.1:{new}")
    return _ENV_LINE.sub(one, text)


def api_override(compose_text: str, env_text: str, published: list[tuple[int, int]],
                 remapped: dict) -> tuple[str, list[str]]:
    """``(override YAML, ["KEY=http://127.0.0.1:P"])``: an override file
    that sets each API-address setting a service reads (in its compose
    environment, or from its env file) to the real address. ("", []) when
    nothing needs it."""
    env = env_values(env_text)
    per: dict[str, dict[str, str]] = {}
    rows = _service_env(compose_text)
    for service, key, value in rows:
        if not key:
            continue
        value = _default_of(value)
        ref = re.fullmatch(r"\$\{?(\w+)\}?", value.strip())
        if ref:
            value = env.get(ref.group(1), "")
        port = _local_port_of(value)
        new = real_port(port, published, remapped) if API_NAME.match(key) and port else 0
        if new:
            per.setdefault(service, {})[key] = f"http://127.0.0.1:{new}"
    # Settings only in its env file, read by a service with env_file.
    readers = [svc for svc, key, value in rows if not key and value == "env_file"]
    for key, value in env.items():
        port = _local_port_of(value)
        new = real_port(port, published, remapped) if API_NAME.match(key) and port else 0
        for service in readers if new else ():
            per.setdefault(service, {}).setdefault(key, f"http://127.0.0.1:{new}")
    if not per:
        return "", []
    lines = ["# Written by BuildBot: the address this app's page uses for its API.",
             "services:"]
    said: list[str] = []
    for service, keys in per.items():
        lines += [f"  {service}:", "    environment:"]
        for k, v in keys.items():
            lines.append(f'      {k}: "{v}"')
            said.append(f"{k}={v}")
    return "\n".join(lines) + "\n", list(dict.fromkeys(said))


# ── compose on Windows ─────────────────────────────────────────────────────

#: Container ports of databases and caches: never the app's page.
_DB_PORTS = frozenset({5432, 3306, 6379, 27017, 11211, 9200, 5672, 1433})
_CONTAINER_PORT = re.compile(r":(\d{2,5})(?:/(?:tcp|udp))?[\"']?\s*(?:#.*)?$")
_PORT_VAR = re.compile(r"\$\{(\w*PORT\w*)(?::?-(\d{2,5}))?\}")
_PORT_LINE = re.compile(r"^(\s*-\s*[\"']?)(?:(\d+\.\d+\.\d+\.\d+|\[[^\]]*\]):)?(\d{2,5}):(\d{2,5})"
                        r"([\"']?.*)$")


def prepare_compose(text: str, *, app_dir: str, port: int,
                    busy: Callable[[int], bool] = lambda _p: False) -> tuple[str, dict]:
    """A compose file's text ready for this computer (BuildBot's copy):
    ``${PWD}`` / ``$PWD`` as the app's folder in C:/ form, the first port
    variable a published port reads set to *port*, and a literal host port
    that is busy here moved to *port*. ``(text, {"published": [(host,
    container)], "remapped": {old: new}, "port_vars": {name: value}})``."""
    pwd = app_dir.replace("\\", "/")
    text = re.sub(r"\$\{PWD(?::?-[^}]*)?\}|\$PWD\b", lambda _m: pwd, text)
    info: dict = {"published": [], "remapped": {}, "port_vars": {}}
    used_port = False
    out: list[str] = []
    in_ports = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"^\s*ports\s*:", line):
            in_ports = True
        elif stripped and not stripped.startswith("-") and not stripped.startswith("#"):
            in_ports = False
        if in_ports and stripped.startswith("-"):
            var = _PORT_VAR.search(line)
            # (a variable in the host part: before the last colon)
            cport = _CONTAINER_PORT.search(line.rstrip())
            if var and not used_port and line.find(var.group(0)) < line.rfind(":") \
                    and not (cport and int(cport.group(1)) in _DB_PORTS):
                info["port_vars"][var.group(1)] = str(port)
                if var.group(2):
                    info["remapped"][int(var.group(2))] = port
                used_port = True
        out.append(line)
    text = "".join(out)
    for name in info["port_vars"]:
        text = re.sub(rf"\$\{{{name}(?::?-[^}}]*)?\}}|\${name}\b", str(port), text)
    out = []
    in_ports = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"^\s*ports\s*:", line):
            in_ports = True
        elif stripped and not stripped.startswith("-") and not stripped.startswith("#"):
            in_ports = False
        m = _PORT_LINE.match(line.rstrip("\r\n")) if in_ports else None
        if m:
            host, container = int(m.group(3)), int(m.group(4))
            if host != port and not used_port and container not in _DB_PORTS and busy(host):
                info["remapped"][host] = port
                used_port = True
                nl = line[len(line.rstrip("\r\n")):]
                ip = f"{m.group(2)}:" if m.group(2) else ""
                line = f"{m.group(1)}{ip}{port}:{container}{m.group(5)}{nl}"
                host = port
            info["published"].append((host, container))
        out.append(line)
    return "".join(out), info


#: A setting that holds a secret (its name), and one that only names where
#: or who (a key file's path, a URL, a host, a port, a user name).
_SECRET_NAME = re.compile(r"(?i)(?:^|_)(?:\w*(?:SECRET|KEY|TOKEN|PASSWORD|PASSWD|SALT)S?|PASS|PWD)$")
_NOT_SECRET_NAME = re.compile(r"(?i)_(?:FILE|PATH|DIR|URL|URI|HOST|HOSTNAME|PORT|USER|USERNAME|"
                              r"NAME|ID|ALGORITHM|ALG|LENGTH|TTL|EXPIRY|EXPIRES\w*)$")
#: A value that is visibly a template default, not a real secret.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:|change[_ .-]?me\w*|\w*change[_ .-]?(?:me|this|it)\w*|your[_ .-]\S*|\S*[_ .-]here"
    r"|\S*example\S*|\S*placeholder\S*|x{3,}|\*{3,}|\.{3,}|secret|password|pass|<[^>]*>"
    r"|\[[^\]]*\]|\{[^}$]*\}|replace[_ .-]?\S*|todo|tbd|insert[_ .-]?\S*|generate[_ .-]?\S*"
    r"|dummy\S*|some[_ .-]?(?:secret|key|password|token)\S*|super[_ .-]?secret\S*)$")
#: Credentials of outside services or optional features (left as they are:
#: a made-up value would only make those calls fail, or switch a feature on).
_OUTSIDE_KEY_NAME = re.compile(
    r"(?i)STRIPE|RECAPTCHA|HCAPTCHA|CAPTCHA|GOOGLE|GITHUB|OIDC|OAUTH|CLIENT_SECRET|SMTP|MAIL|"
    r"LICENSE|VAPID|PUBLIC|SSH|SORT|AWS|S3|TWILIO|SENDGRID|OPENAI|ANTHROPIC|SLACK|DISCORD|"
    r"(?:API_?KEY|ACCESS_KEY|ACCESS_TOKEN|_TOKEN)$")
_ENV_ASSIGN = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_]\w*)(\s*=\s*)([\"']?)(.*?)\4(\s+#.*)?\s*$")


def secret_setting(name: str) -> bool:
    """Is *name* a setting that holds this app's own secret (not a path,
    URL, host, port or user, nor an outside service's credential)?"""
    return (bool(_SECRET_NAME.search(name or "")) and not _NOT_SECRET_NAME.search(name or "")
            and not _OUTSIDE_KEY_NAME.search(name or ""))


def fill_env_secrets(text: str, new: Callable[[], str]) -> tuple[str, dict]:
    """``(text, filled)``: an env file's text with each secret setting whose
    value is a template placeholder (``changeme``, ``your_..._here``; never
    an empty one) given a fresh value from *new*. One placeholder gets one
    new value everywhere in the file: in each setting that had it, and as a
    whole word inside other values (``postgres://app:changeme@db/app``), so
    settings that must match still match. *filled* is ``{name: value}`` of
    each setting it changed (the new value inside it), so the values can be
    kept out of what is shown or sent (and put back where they were copied).
    Everything else stays."""
    lines = (text or "").splitlines(keepends=True)
    made: dict[str, str] = {}               # placeholder -> its new value
    for line in lines:
        m = _ENV_ASSIGN.match(line.rstrip("\r\n"))
        value = m.group(5).strip() if m else ""
        if m and value and secret_setting(m.group(2)) and _PLACEHOLDER.match(value):
            if value not in made:
                made[value] = new()
    if not made:
        return text, {}
    inside = re.compile(r"(?<![\w.-])(" + "|".join(
        re.escape(k) for k in sorted(made, key=len, reverse=True)) + r")(?![\w.-])")
    filled: dict[str, str] = {}
    out: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        m = _ENV_ASSIGN.match(body)
        if m:
            value = m.group(5).strip()
            used: list[str] = []
            if value in made and secret_setting(m.group(2)):
                new_value = made[value]
                used.append(new_value)
            elif value not in made:
                new_value = inside.sub(lambda h: used.append(made[h.group(1)])
                                       or made[h.group(1)], m.group(5))
            else:
                new_value = m.group(5)          # (a non-secret setting set to it: as it is)
            if used:
                filled[m.group(2)] = used[0]
                body = (f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{new_value}"
                        f"{m.group(4)}{m.group(6) or ''}")
                line = body + line[len(line.rstrip("\r\n")):]
        out.append(line)
    return "".join(out), filled


def example_copies(folder: str, readme: str = "", compose_text: str = "") -> list[tuple[str, str]]:
    """``[(example, target)]`` (paths inside *folder*) to copy before it
    starts: .env.example (or .sample/.dist) to .env when that folder has no
    .env yet (one already written is never doubled or overwritten), and
    each config file its README or compose file names that is missing while
    an example of it (``x.example``, ``x.sample``, ``x.dist``) or a file of
    the same name elsewhere in the app exists."""
    out: list[tuple[str, str]] = []
    if not os.path.exists(os.path.join(folder, ".env")):
        for n in (".env.example", ".env.sample", ".env.dist", ".env.template", "example.env"):
            if os.path.isfile(os.path.join(folder, n)):
                out.append((n, ".env"))
                break
    named = set(re.findall(r"(?<![\w/.-])\.?/?((?:[\w-]+/)+[\w.-]+\.(?:ya?ml|json|toml|ini|conf|"
                           r"env|cfg))\b", f"{readme}\n{compose_text}"))
    for rel in sorted(named):
        rel = rel.lstrip("./")
        target = os.path.join(folder, rel)
        if os.path.exists(target) or ".." in rel:
            continue
        # Only a file its compose file mounts, or one its README says to copy or create.
        if rel not in compose_text and not re.search(
                r"(?i)\b(?:cp|copy|rename|create|must exist)\b[^\n]{0,100}" + re.escape(rel),
                readme or ""):
            continue
        src = next((rel + s for s in (".example", ".sample", ".dist")
                    if os.path.isfile(os.path.join(folder, rel + s))), "")
        if not src:
            src = _same_name(folder, os.path.basename(rel))
        if src:
            out.append((src, rel))
    return out


def _same_name(folder: str, name: str) -> str:
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
        if os.path.relpath(dirpath, folder).count(os.sep) >= 3:
            dirnames[:] = []
        if name in filenames:
            return os.path.relpath(os.path.join(dirpath, name), folder).replace(os.sep, "/")
    return ""


# ── the working check ──────────────────────────────────────────────────────

# ── the main page of a multi-service app ───────────────────────────────────

#: Compose services that are usually the page a person opens.
#: Best first: the service in front of everything (a proxy), then the page itself.
MAIN_SERVICES = ("proxy", "gateway", "nginx", "caddy", "traefik", "web", "app", "frontend",
                 "ui")
#: Services that serve a page, but not the app's own: tried last, and never
#: taken while one of MAIN_SERVICES is in the project (up or down).
LAST_SERVICES = ("www", "docs", "doc", "site", "landing", "marketing", "metrics", "api",
                 "admin", "grafana", "prometheus")
_SIDE_SERVICE = re.compile(r"(?i)metrics|prometheus|exporter|collector|jaeger|zipkin|otel|"
                           r"kafka|redis|postgres|mysql|mongo|valkey|loadgen")
_OPEN_URL = re.compile(r"(?i)https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::(\d{2,5}))?"
                       r"(/[^\s)`'\"<>\]]*)?")
_OPEN_WORDS = re.compile(r"(?i)\b(?:open|browse|browser|visit|navigate|go to|access|"
                         r"available at|reach|point)\b")


def readme_open_ports(readme: str) -> list[int]:
    """The local ports its README says to open ("open your browser at
    http://localhost:8080/"), lines that say open/visit first."""
    said, other = [], []
    for line in str(readme or "").splitlines():
        for m in _OPEN_URL.finditer(line):
            port = int(m.group(1) or 80)
            if (m.group(2) or "").startswith("/metrics"):
                continue
            (said if _OPEN_WORDS.search(line) else other).append(port)
    return list(dict.fromkeys(said + other))


def _words(service: str) -> list[str]:
    return re.split(r"[-_.\s]+", str(service or "").lower())


def side_service(service: str) -> bool:
    """A service that is not the app's own page (its marketing site, docs,
    API, admin, metrics, a database)."""
    return bool(any(w in LAST_SERVICES for w in _words(service))
                or _SIDE_SERVICE.search(str(service or "")))


def main_service(service: str) -> bool:
    """A service named like the app's front page (proxy, web, frontend...)."""
    return any(w in MAIN_SERVICES for w in _words(service)) and not side_service(service)


def _service_rank(service: str) -> tuple[int, int]:
    if main_service(service):
        return (1, min(MAIN_SERVICES.index(w) for w in _words(service) if w in MAIN_SERVICES))
    if side_service(service):
        return (3, 0)
    return (2, 0)


def readme_host_ports(readme: str, pairs: Any = (), remapped: Optional[dict] = None) -> list[int]:
    """The ports its README says to open, each with the host port it has
    here (*pairs* ``[(host, container)]``, *remapped* ``{old: new}``)."""
    remapped = remapped or {}
    to_host: dict[int, int] = {}
    for host, container in pairs or ():
        to_host.setdefault(int(container), int(host))
    said: list[int] = []
    for p in readme_open_ports(readme):
        said += [p, int(remapped.get(p, p)), to_host.get(p, p)]
    return said


def main_page_order(ports: list[int], services: Optional[dict] = None, readme: str = "",
                    pairs: Any = (), remapped: Optional[dict] = None) -> list[int]:
    """*ports* (published on this computer) in the order the page check
    tries them: the one the README says to open (mapped to its published
    host port: *pairs* ``[(host, container)]``, *remapped* ``{old: new}``),
    then a compose service named like a front page (*services* ``{port:
    name}``), then the others; metrics and data services last."""
    services = services or {}
    said = readme_host_ports(readme, pairs, remapped)

    def rank(port: int) -> tuple:
        if port in said:
            return (0, said.index(port))
        return _service_rank(services.get(port, ""))
    return sorted(dict.fromkeys(int(p) for p in ports), key=rank)


_NOT_MAIN_PATH = re.compile(r"(?i)^/(?:metrics|actuator|healthz?|livez|readyz|-/)\b")
_NOT_MAIN_TITLE = re.compile(r"(?i)\b(?:prometheus|grafana agent|exporter|"
                             r"whitelabel error page)\b")
_EXPORTER_LINE = re.compile(r"^(?:# (?:HELP|TYPE) \S+.*|[A-Za-z_:][\w:]*(?:\{[^}]*\})?\s+"
                            r"[-+]?(?:[\d.]+(?:[eE][-+]?\d+)?|NaN|\+?Inf))(?:\s+\d+)?$")


def not_main_page(page: dict, url: str = "", app_name: str = "") -> str:
    """Why *page* looks like a metrics or API endpoint, not the page a person
    opens (a /metrics path, a Prometheus or exporter title, pure JSON, plain
    exporter lines, a Whitelabel error), or ""."""
    from urllib.parse import urlparse
    path = urlparse(str(url or "")).path or "/"
    if _NOT_MAIN_PATH.search(path):
        return f"{path} is a metrics or health address"
    title = str((page or {}).get("title") or "")
    text = str((page or {}).get("text") or "").strip()
    head = f"{title}\n{text[:300]}"
    word = _NOT_MAIN_TITLE.search(head)
    if word and word.group(0).lower() not in str(app_name or "").lower():
        return f"it is titled {title or word.group(0)!r} (a metrics or error page)"
    if text[:1] in "{[":
        try:
            json.loads(text)
            return "it answers plain JSON (an API)"
        except ValueError:
            pass
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and sum(bool(_EXPORTER_LINE.match(ln)) for ln in lines) >= max(3, len(lines) * 0.6):
        return "it shows plain metrics lines (an exporter)"
    return ""


_CRED_HEAD = re.compile(r"(?im)^#+[^\n]*\b(?:login|log in|sign in|credentials|demo|default "
                        r"(?:user|account|password|credentials)|usage|getting started)\b")
_CRED_PAIR = re.compile(r"(?i)(?:`|\*\*|\b)([\w.@-]{2,40})[`*]*\s*/\s*[`*]*([\w.@!#$%^&-]{2,40})"
                        r"(?:`|\*\*|\b)")
_CRED_KV = re.compile(r"(?is)\b(?:user(?:name)?|login|email)\b\s*[:=]?\s*[`*\"']*([\w.@-]{2,40})"
                      r"[`*\"']*.{0,60}?\bpass(?:word)?\b\s*[:=]?\s*[`*\"']*([\w.@!#$%^&*-]{2,40})")
_CRED_WORDS = re.compile(r"(?i)\b(?:login|log in|log-in|sign in|sign-in|username|user|"
                         r"password|passwd|credentials|default account)\b")
_CRED_URL = re.compile(r"(?i)\b(?:https?|git|ssh)://\S+|\bgit@\S+|"
                       r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|io|net|dev|app)/\S*")
_CRED_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_GIT_CLONE = re.compile(r"(?i)\bgit\s+clone\b")


def _cred_text(readme: str) -> str:
    """*readme* without what never holds a sign-in: web and git addresses,
    owner/repo paths in them, git clone lines and code blocks with git clone."""
    text = _CRED_FENCE.sub(lambda m: "\n" * m.group(0).count("\n")
                           if _GIT_CLONE.search(m.group(0)) else m.group(0), str(readme or ""))
    return "\n".join("" if _GIT_CLONE.search(ln) else _CRED_URL.sub(" ", ln)
                     for ln in text.splitlines())


def readme_credentials(readme: str) -> Optional[tuple[str, str]]:
    """``(user, password)`` its README documents for a demo or default
    login ("Login: demo/demo", "username: admin, password: admin"), or None.
    Only from text that says login, sign in, user, password or credentials;
    never from addresses, owner/repo paths or git clone lines."""
    text = _cred_text(readme)
    # owner/repo paths its addresses name ("github.com/owner/repo"): never a pair.
    repos = {f"{a}/{b}".lower() for a, b in re.findall(
        r"(?i)\b[\w-]+(?:\.[\w-]+)*\.\w{2,}/([\w.-]+)/([\w-]+)", str(readme or ""))}
    for m in re.finditer(r"[^\n]*(?:\n[^\n]*){0,3}", text):
        chunk = m.group(0)
        if not _CRED_WORDS.search(chunk):
            continue
        kv = _CRED_KV.search(chunk)
        if kv:
            return kv.group(1), kv.group(2)
        for p in _CRED_PAIR.finditer(chunk):
            user, pw = p.group(1), p.group(2)
            if not re.search(r"(?i)https?|\.com|\.\w{2,4}$", user) and not user.isdigit() \
                    and "/" not in chunk[max(0, p.start() - 1):p.start()] \
                    and chunk[p.end():p.end() + 1] != "/" \
                    and f"{user}/{pw}".lower() not in repos:
                return user, pw
    return None


def sign_in_line(creds: tuple[str, str]) -> str:
    return f"Sign in with {creds[0]} / {creds[1]} (from its README)"


_BANNER = re.compile(r"(?i)\b(?:failed to fetch|unable to\b|cannot connect|can.t connect|"
                     r"could not connect|network error|error)\b")


def banners(text: str) -> list[str]:
    """Every error banner line a loaded page shows ("Failed to fetch",
    "Unable to load...", a widget's "ERROR"), short lines only."""
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if 0 < len(line) <= 140 and _BANNER.search(line):
            out.append(line)
    return out


def banner(text: str) -> str:
    """The first error banner a loaded page shows, as its own short line, or ""."""
    got = banners(text)
    return got[0] if got else ""


_REQ = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b\s+\"?([^\s\"]+)\"?"
                  r"(?:\s+HTTP/[\d.]+\"?)?[^\n]{0,40}?(?:\s|=|\"|->\s?)([1-5]\d\d)\b")
_KV_REQ = re.compile(r"(?i)\b(?:path|uri|url)=\"?(\S+?)\"?\s.*?\bstatus=(\d{3})|"
                     r"\bstatus=(\d{3})\b.*?\b(?:path|uri|url)=\"?(\S+?)\"?(?:\s|$)")
_STATIC = re.compile(r"(?i)\.(?:js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|html?|"
                     r"txt|json\.map)(?:\?|$)|^/(?:_next/static|assets|static)/|^/$")
_OUTSIDE = re.compile(r"https?://((?!localhost|127\.0\.0\.1)[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
#: An outside host that failed: it answered 4xx/5xx (refused, blocked, down).
_OUTSIDE_FAIL = re.compile(r"\b[45]\d\d\b|service unavailable|bad gateway|gateway timeout|"
                           r"forbidden|too many requests|rate limit", re.I)
#: A banner that names an outside service: "403 from api.example.com",
#: "Reddit returned 403", or any web address that is not this computer.
_NAMED_FROM = re.compile(r"(?i)\b[45]\d\d\b[^\n]{0,40}?\bfrom\s+([A-Za-z][\w.-]*[A-Za-z])")
_NAMED_SAID = re.compile(r"\b([A-Z][\w.-]*[A-Za-z])\s+(?:returned|answered|responded with|"
                         r"gave|says?)\s+(?:an?\s+)?(?:error\s+)?(?:HTTP\s+)?[45]\d\d\b")
_DOMAIN = re.compile(r"(?i)\b((?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|app|co|tv|fm|me|info))\b")


def request_counts(log: str, lines: int = 50) -> dict:
    """From the last *lines* of an app's log: its own API calls that
    answered 2xx (``ok``) and 4xx/5xx (``bad``, with the first such line),
    and outside hosts that answered 4xx/5xx (``down``)."""
    tail = str(log or "").splitlines()[-lines:]
    got = {"ok": 0, "bad": 0, "bad_line": "", "down": []}
    for line in tail:
        m = _REQ.search(line)
        if m:
            path, status = m.group(2), int(m.group(3))
        else:
            k = _KV_REQ.search(line)
            path = (k.group(1) or k.group(4)) if k else ""
            status = int(k.group(2) or k.group(3)) if k else 0
        own = bool(path) and not path.startswith("http")
        outside = None if own else _OUTSIDE.search(line)
        if outside and _OUTSIDE_FAIL.search(line):
            host = outside.group(1)
            if host not in got["down"]:
                got["down"].append(host)
            continue
        if not own or _STATIC.search(path.split("?")[0]):
            continue
        if 200 <= status < 400:
            got["ok"] += 1
        elif status >= 400:
            got["bad"] += 1
            if not got["bad_line"]:
                got["bad_line"] = line.strip()[:200]
    return got


def outside_named(line: str, own_name: str = "") -> str:
    """The outside service an error banner names ("403 from api.example.com",
    "Reddit returned 403", a web address), or ""."""
    for rx in (_OUTSIDE, _NAMED_FROM, _NAMED_SAID, _DOMAIN):
        m = rx.search(str(line or ""))
        if m and m.group(1).lower() not in ("localhost", "127.0.0.1") \
                and m.group(1).lower() not in str(own_name or "").lower():
            return m.group(1)
    return ""


def judge(page_text: str, log: str, own_name: str = "") -> dict:
    """The working check after a page loaded: ``{"working", "said",
    "note"}``. Only errors from its own server or API count. Error banners
    that come from outside services (an outside host failing in its log, or
    the banner names one) leave it working, with a note for her."""
    counts = request_counts(log)
    lines = banners(page_text)
    named = [outside_named(line, own_name) for line in lines]
    hosts = list(dict.fromkeys(counts["down"] + [n for n in named if n]))
    # Banners an outside service explains: all of them when its log shows an
    # outside host failing, else the ones that name an outside service.
    own_lines = [] if counts["down"] else [ln for ln, n in zip(lines, named) if not n]
    shown = own_lines[0] if own_lines else ""
    note = f"{hosts[0]} is not answering right now; the app works" if hosts else ""
    if counts["bad"]:
        if shown or (lines and not hosts) or not counts["ok"]:
            shown = shown or (lines[0] if lines and not hosts else "")
            return {"working": False, "note": "",
                    "said": f"Its page shows {shown!r}; its own server log: "
                            f"{counts['bad_line']}" if shown else
                            f"Its own API calls fail in its server log: {counts['bad_line']}"}
    if shown and not counts["ok"]:
        return {"working": False, "note": "",
                "said": f"Its page shows {shown!r}, and its server log shows no API call "
                        "reaching it: its page may be told the wrong address for its API."}
    return {"working": True, "note": note, "said": ""}
