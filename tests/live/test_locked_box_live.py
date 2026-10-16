"""Live: the locked box on this computer. The proof transcripts
``design/sandbox/00-19`` as assertions, plus setup from scratch, a real npm
web-app build and a real PyPI command-line tool.

Runs only with ``PUFFPAI_R3_LIVE=1`` and ``PUFFPAI_BOX_LIVE=1`` and
``PUFFPAI_BOX_DISTRO=PuffPAI-sandbox-dev`` (the ONLY distro this may touch),
plus:

* ``PUFFPAI_BOX_ROOT``: the development box root (inside the scratchpad);
* ``PUFFPAI_L7_LIVE_STATE``: the state folder whose claim owns the box;
* ``PUFFPAI_L7_LIVE_SCRATCH``: a scratch folder for downloads and owner folders;
* ``PUFFPAI_BOX_LIVE_SETUP=1`` to remove and set the box up again from scratch
  first (that is the proof of the setup path).

Never ``wsl --shutdown``; terminate/unregister only through the driver's guard.
Run with ``-s`` to keep the printed transcript.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import threading
import time

import pytest

from puffpai import source_fetch
from puffpai.box import cli_tools, driver, locks, wiring
from puffpai.box import wording as W
from puffpai.security import consent_tickets as ct
from puffpai.security import owner_folders

_REAL_TRANSPORT = source_fetch._default_transport
DISTRO = "PuffPAI-sandbox-dev"
TABLEAUX = ("https://codeload.github.com/JFrusher/Tableaux/tar.gz/"
            "10312ad24724551ea715416a134ac16fe5c556c2")
TABLEAUX_SHA_PREFIX = "657054837a292d37"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (os.environ.get("PUFFPAI_R3_LIVE") == "1"
                            and os.environ.get("PUFFPAI_BOX_LIVE") == "1"
                            and os.environ.get("PUFFPAI_BOX_DISTRO") == DISTRO
                            and os.environ.get("PUFFPAI_BOX_ROOT")
                            and os.environ.get("PUFFPAI_L7_LIVE_STATE")
                            and os.environ.get("PUFFPAI_L7_LIVE_SCRATCH")),
                       reason="live box check: see the module docstring"),
]


def say(*parts) -> None:
    text = " ".join(str(p) for p in parts)
    print("[L7 live]", text.encode("ascii", "replace").decode("ascii"), flush=True)


@pytest.fixture
def live(monkeypatch):
    scratch = os.environ["PUFFPAI_L7_LIVE_SCRATCH"]
    monkeypatch.setenv("PUFFPAI_STATE_DIR", os.environ["PUFFPAI_L7_LIVE_STATE"])
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", DISTRO)
    monkeypatch.setattr(source_fetch, "_default_transport", _REAL_TRANSPORT)
    from puffpai import config
    config.get_config(_force_reload=True)
    if not wiring.REGISTERED:
        from puffpai import drawbridge
        if not drawbridge.registration_closed():
            wiring.connect()
    profile = os.path.join(scratch, "profile")
    folders = {}
    for name in owner_folders.READ_ROOT_NAMES:
        os.makedirs(os.path.join(profile, name), exist_ok=True)
        folders[name] = [os.path.join(profile, name)]
    monkeypatch.setattr(owner_folders, "known_folders", lambda: folders)
    ct.reset()
    return {"scratch": scratch, "profile": profile}


def root(cmd: str, *, timeout: float = 120) -> str:
    r = driver._wsl(["-d", DISTRO, "-u", "root", "--exec", "/bin/sh", "-c", cmd], timeout=timeout)
    return driver._text(r.stdout) + driver._text(r.stderr)


def jailed(project: str, script: str, **kw) -> driver.BoxResult:
    req = driver.BoxRequest(run_id=driver.new_run_id(), project=project,
                            argv=["/bin/sh", "-c", script], **kw)
    return driver.run(req)


# ── setup and status ─────────────────────────────────────────────────────────

def test_00_setup_from_scratch(live):
    if os.environ.get("PUFFPAI_BOX_LIVE_SETUP") != "1":
        pytest.skip("set PUFFPAI_BOX_LIVE_SETUP=1 to set the box up again from scratch")
    if driver._entries_named(DISTRO):
        got = driver.remove_box()
        say("remove_box:", got)
        assert got["removed"], got
    assert not driver._entries_named(DISTRO)
    device = ct.terminal_device_id()
    ticket = ct.mint("box_setup", ct.target_box(DISTRO), "Set up the locked box.", device)
    lines: list = []
    t0 = time.monotonic()
    st = driver.setup(ticket=ticket, device_id=device, progress=lines.append)
    took = time.monotonic() - t0
    say("setup lines:", lines)
    say(f"SETUP SECONDS: {took:.1f}", st.as_dict())
    assert st.ready, st
    assert lines[0] == W.SETUP_DOWNLOADING and lines[-1] == W.SETUP_DONE


def test_01_status_ready_and_the_guard(live):
    st = driver.status()
    say("status:", st.as_dict())
    assert st.ready and st.distro == DISTRO
    assert driver.guard(DISTRO) == (True, "")
    for other in ("Ubuntu", "jetson-flash", "docker-desktop"):
        assert driver.guard(other)[0] is False


# ── 01/03/04/06: the distro itself is locked ────────────────────────────────

def test_02_the_distro_is_locked(live):
    out = root("cat /etc/wsl.conf; echo ==; cat /etc/puffpai-box.json; echo; echo ==;"
               " awk '$2 ~ \"^/mnt/\" && $2 !~ \"^/mnt/wsl\"' /proc/mounts | wc -l; echo ==;"
               " find / -xdev -perm -4000 -type f | wc -l; echo ==; grep '^root:' /etc/shadow"
               " | cut -d: -f2; echo ==; echo $PATH; echo ==; command -v cmd.exe powershell.exe"
               " || echo none; echo ==; stat -c '%U %a' /opt/puffpai/box-run /opt/puffpai/jail.py")
    say("distro lock:\n" + out)
    parts = [p.strip() for p in out.split("==")]
    assert "[automount]\nenabled = false" in parts[0] and "[interop]\nenabled = false" in parts[0]
    assert json.loads(parts[1])["distro"] == DISTRO
    assert parts[2] == "0"                               # no Windows drive mounted
    assert parts[3] == "0"                               # no setuid programs
    assert parts[4] in ("*", "!", "!*")                  # root cannot log in
    assert "/mnt/" not in parts[5]
    assert parts[6] == "none"
    assert parts[7].splitlines() == ["root 755", "root 644"]
    plain = subprocess.run([driver.wsl_exe(), "-d", DISTRO], capture_output=True, timeout=60,
                           **driver._run_kwargs())
    assert "not available" in driver._text(plain.stdout + plain.stderr)


def test_03_the_self_test_passes(live):
    assert driver.self_test(DISTRO) is True


# ── 07/08: inside a run ──────────────────────────────────────────────────────

PROBE = r"""
echo "id=$(id -u)"; echo "ls=$(ls / | tr '\n' ' ')"; echo "mnt=$(ls /mnt 2>&1)"
echo "etc=$(ls /etc | tr '\n' ' ')"; echo "run=$(ls /run 2>&1)"; echo "srv=$(ls /srv 2>&1)"
echo "ifs=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | tr '\n' ' ')"
touch /usr/x 2>/dev/null && echo usr=writable || echo usr=ro
touch /x 2>/dev/null && echo root=writable || echo root=ro
touch /work/ok && echo work=ok; touch /tmp/ok && echo tmp=ok
unshare -U true 2>/dev/null && echo userns=yes || echo userns=no
grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status | tr '\t' ' '
echo "unix=$(wc -l < /proc/net/unix)"; echo "resolv=$(cat /etc/resolv.conf 2>&1 | head -1)"
echo "interop=[$WSL_INTEROP]"
python3 -c "import ctypes,os; l=ctypes.CDLL(None,use_errno=True); r=l.syscall(101,16,1,0,0); print('ptrace', r, ctypes.get_errno())"
python3 -c "import ctypes,os; l=ctypes.CDLL(None,use_errno=True); r=l.syscall(321,0,0,0); print('bpf', r, ctypes.get_errno())"
python3 -c "import fcntl,termios,sys; import os
try:
    fcntl.ioctl(0, termios.TIOCSTI, b'x'); print('tiocsti ok')
except OSError as e: print('tiocsti', e.errno)"
"""


def test_04_isolation_inside_a_run(live):
    r = jailed("l7-probe", PROBE, limits={"wall_s": 60})
    say("isolation:\n" + r.stdout_tail)
    assert r.ok, r
    out = dict(line.split("=", 1) for line in r.stdout_tail.splitlines() if "=" in line
               and not line.startswith(("CapEff", "NoNewPrivs", "Seccomp")))
    assert out["id"] == "61000"
    assert out["ls"].split() == ["bin", "dev", "etc", "lib", "opt", "proc", "tmp", "usr", "work"]
    assert "No such file" in out["mnt"] and "No such file" in out["run"] and "No such file" in out["srv"]
    assert out["etc"].split() == ["group", "hosts", "passwd", "ssl"]
    assert out["ifs"].split() == ["lo"]
    assert out["usr"] == "ro" and out["root"] == "ro" and out["work"] == "ok" and out["tmp"] == "ok"
    assert out["userns"] == "no" and out["interop"] == "[]"
    assert "CapEff: 0000000000000000" in r.stdout_tail and "NoNewPrivs: 1" in r.stdout_tail
    assert "Seccomp: 2" in r.stdout_tail
    assert out["unix"].strip() == "1"                           # header only
    assert "No such file" in out["resolv"]
    assert "ptrace -1 1" in r.stdout_tail and "bpf -1 1" in r.stdout_tail   # EPERM
    assert "tiocsti ok" not in r.stdout_tail


NET = r"""
for t in "127.0.0.1 11435" "1.1.1.1 443" "8.8.8.8 53" "192.168.1.1 80" "10.255.255.254 53" "172.17.0.1 80"; do
  set -- $t
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(3)
try:
    s.connect(('$1',$2)); print('OPEN $1 $2')
except OSError as e: print('closed $1 $2', e.errno)"
done
curl -s -m 5 -o /dev/null -w '%{http_code}' https://registry.npmjs.org/; echo " curl_exit=$?"
"""


def test_05_no_network_inside_a_run(live):
    r = jailed("l7-probe", NET, limits={"wall_s": 90})
    say("network:\n" + r.stdout_tail)
    assert r.ok and "OPEN" not in r.stdout_tail
    assert "curl_exit=0" not in r.stdout_tail


# ── 09: limits ───────────────────────────────────────────────────────────────

def test_06_limits(live):
    mem = jailed("l7-limits", "python3 -c \"b=[bytearray(64*1024*1024) for _ in range(64)]\"",
                 limits={"mem_mb": 256, "wall_s": 60})
    assert mem.oom_killed and not mem.ok and mem.mem_peak_mb <= 257
    pids = jailed("l7-limits", "i=0; while [ $i -lt 400 ]; do sleep 30 & i=$((i+1)); done; wait",
                  limits={"pids": 64, "wall_s": 10})
    assert pids.pids_peak <= 64 and not pids.ok
    cpu = jailed("l7-limits", "python3 -c 'while True: pass'", limits={"cpu_s": 3, "wall_s": 60})
    assert not cpu.ok and not cpu.timed_out and cpu.wall_s < 30
    wall = jailed("l7-limits", "sleep 600", limits={"wall_s": 5})
    assert wall.timed_out and 4.5 <= wall.wall_s <= 8
    fsize = jailed("l7-limits", "dd if=/dev/zero of=/work/big bs=1M count=50 2>/dev/null;"
                   " stat -c %s /work/big; rm -f /work/big", limits={"fsize_mb": 10, "wall_s": 30})
    assert "10485760" in fsize.stdout_tail
    disk = jailed("l7-disk", "for i in 1 2 3 4 5 6 7 8; do dd if=/dev/zero of=/work/f$i bs=1M"
                  " count=16 2>/dev/null || { echo full at $i; break; }; done; rm -f /work/f*",
                  limits={"disk_mb": 64, "wall_s": 60})
    say("limits:", [(x.ok, x.oom_killed, x.timed_out, x.wall_s, x.pids_peak)
                    for x in (mem, pids, cpu, wall)], fsize.stdout_tail.strip(), disk.stdout_tail)
    assert "full at" in disk.stdout_tail
    assert "run-" not in root("ls /sys/fs/cgroup/puffpai-box")
    driver.remove_project("l7-disk")
    driver.remove_project("l7-limits")


# ── 10/19: the outside proxy ─────────────────────────────────────────────────

PROXY = r"""
c(){ label="$1"; shift; code=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$@"); echo "$label http=$code exit=$?"; }
c allowed https://registry.npmjs.org/
c other https://example.com/
c ip https://1.1.1.1/
c ipdec https://2130706433/
c ip6 "https://[::1]/"
c inside_name https://localtest.me/
c inside_nip https://127.0.0.1.nip.io/
c plain_http -x http://127.0.0.1:3128 http://registry.npmjs.org/
c other_port https://registry.npmjs.org:8443/
c bypass --noproxy '*' https://registry.npmjs.org/
c sni --connect-to example.com:443:registry.npmjs.org:443 https://example.com/
echo "node $(node -e "fetch('https://registry.npmjs.org/left-pad').then(r=>console.log(r.status)).catch(e=>console.log('err'))")"
echo "sock $(printf 'CONNECT 127.0.0.1:22 HTTP/1.1\r\nHost: x\r\n\r\n' | socat -t3 - UNIX-CONNECT:/run/egress.sock | head -1)"
ls /var/log/puffpai 2>&1 | head -1
"""


def test_07_the_proxy_allows_only_its_list_and_logs_every_try(live, monkeypatch):
    from puffpai.security import egress_ledger
    monkeypatch.setattr(driver, "_runtime_hosts_allowed", lambda req: True)  # the owner's switch
    # Only lines written from now on count: a count over a sliding 1-hour
    # window fails when older lines age out during the run (validator item).
    from collections import Counter
    from datetime import datetime, timezone
    started = datetime.now(timezone.utc).replace(microsecond=0)
    view: dict = {}

    def watch():
        time.sleep(4)
        view["proxy"] = root("p=$(pgrep -u 61001 python3 | head -1); echo pid=$p;"
                             " ls /proc/$p/root | tr '\\n' ' '; echo; ls /proc/$p/root/mnt 2>&1;"
                             " ls /proc/$p/root/srv 2>&1; grep -E 'NoNewPrivs|Seccomp' /proc/$p/status")
    t = threading.Thread(target=watch)
    t.start()
    r = jailed("l7-proxy", "sleep 8; " + PROXY, what="box_traffic", limits={"wall_s": 120},
               allow_hosts=["registry.npmjs.org", "localtest.me", "127.0.0.1.nip.io"])
    t.join()
    say("proxy:\n" + r.stdout_tail, "\nproxy jail:\n" + view.get("proxy", ""))
    out = {ln.split()[0]: ln for ln in r.stdout_tail.splitlines() if ln.strip()}
    assert "http=200" in out["allowed"]
    for key in ("other", "ip", "ipdec", "ip6", "inside_name", "inside_nip", "other_port",
                "bypass", "sni"):
        assert "http=200" not in out[key], out[key]
    assert "http=403" in out["plain_http"]
    assert out["node"] == "node 200" and "403" in out["sock"]
    assert "No such file" in r.stdout_tail.splitlines()[-1]         # the log is not visible
    codes = [(o.get("host"), o.get("outcome"), o.get("code")) for o in r.outside]
    say("proxy lines:", codes)
    assert ("registry.npmjs.org", "done", "") in codes
    assert ("example.com", "refused", "not_on_list") in codes
    assert ("localtest.me", "refused", "inside_address") in codes
    assert any(c == ("registry.npmjs.org", "refused", "not_allowed") for c in codes)   # SNI
    assert any(o == "refused" and c == "not_encrypted" for _h, o, c in codes)
    labels = {driver.service_for("l7-proxy"), "box_traffic"}
    logged = Counter((o["host"], o["outcome"], o["code"])
                     for o in egress_ledger.read(1.0, limit=5000)
                     if o["service"] in labels
                     and datetime.fromisoformat(o["t"]) >= started)
    # Each proxy line as the owner's log keeps it (the same checks ingest runs).
    want = Counter()
    for o in r.outside:
        rec = egress_ledger.build_record(
            "box_traffic", host=str(o.get("host", "")), outcome=str(o.get("outcome", "")),
            code=str(o.get("code", "")), what=str(o.get("what", "")))
        if rec is not None:
            want[(rec["host"], rec["outcome"], rec["code"])] += 1
    assert sum(want.values()) == len(codes), want                # every line is loggable
    missing = want - logged
    assert not missing, missing                                  # all in the owner's log
    pv = view.get("proxy", "").replace("\t", " ")
    listing = pv.splitlines()[1].split()
    assert "bin" in listing and "mnt" not in listing and "srv" not in listing
    assert "NoNewPrivs: 1" in pv and "Seccomp: 2" in pv


# ── 11/16: owner folders ─────────────────────────────────────────────────────

def test_08_owner_folders_read_only_read_write_and_refusals(live):
    import _winapi
    pics = os.path.join(live["profile"], "Pictures", "Party")
    os.makedirs(pics, exist_ok=True)
    with open(os.path.join(pics, "cake.txt"), "w", encoding="utf-8") as fh:
        fh.write("picture-bytes")
    ro = jailed("l7-folder", "ls /data/files; cat /data/files/cake.txt; echo; touch /data/files/new"
                " 2>&1; ls /data/files/.. 2>&1 | tr '\\n' ' '",
                folder={"windows_path": pics, "mode": "ro", "name": "files"}, limits={"wall_s": 60})
    say("folder ro:\n" + ro.stdout_tail + ro.stderr_tail)
    assert ro.ok and "cake.txt" in ro.stdout_tail and "picture-bytes" in ro.stdout_tail
    assert "Read-only file system" in ro.stdout_tail
    assert not os.path.exists(os.path.join(pics, "new"))
    # A folder holding a junction to the vault is never given.
    vault = owner_folders.vault_dir()
    os.makedirs(vault, exist_ok=True)
    bad = os.path.join(live["profile"], "Pictures", "Album")
    os.makedirs(bad, exist_ok=True)
    link = os.path.join(bad, "inner")
    if not os.path.lexists(link):
        _winapi.CreateJunction(vault, link)
    refused = jailed("l7-folder", "ls /data/files",
                     folder={"windows_path": bad, "mode": "ro", "name": "files"})
    assert refused.refused == "folder" and refused.error == W.FOLDER_PRIVATE
    os.rmdir(link)
    for private in (vault, os.environ["PUFFPAI_L7_LIVE_STATE"], live["profile"]):
        r = jailed("l7-folder", "true", folder={"windows_path": private, "mode": "ro", "name": "f"})
        assert r.refused == "folder", private
    # Read-write: only BuildBot's own folder; what an app leaves is made safe.
    made = owner_folders.write_folder("Party Bot", "Cards")
    rw = jailed("l7-folder", "echo hi > /data/files/new.txt; echo x > /data/files/run.ps1;"
                " ln -s /etc/passwd /data/files/evil_link; chmod +x /data/files/new.txt;"
                " /data/files/new.txt; echo exec=$?",
                folder={"windows_path": made, "mode": "rw", "name": "files"}, limits={"wall_s": 60})
    say("folder rw:\n" + rw.stdout_tail, rw.removed_new_links)
    assert rw.ok and rw.removed_new_links == 1 and "exec=126" in rw.stdout_tail
    names = sorted(os.listdir(made))
    assert "new.txt" in names and "run.ps1" not in names and "run.ps1.blocked" in names
    assert "evil_link" not in names
    assert os.path.exists(os.path.join(made, "new.txt") + owner_folders.ZONE_STREAM)
    with pytest.raises(driver.BoxRefused):
        driver.validate(driver.BoxRequest(run_id="r1", project="x", argv=["true"],
                                          what="box_install",
                                          folder={"windows_path": made, "mode": "rw",
                                                  "name": "files"}))
    rw2 = jailed("l7-folder", "true", folder={"windows_path": pics, "mode": "rw", "name": "f"})
    assert rw2.refused == "folder" and rw2.error == W.FOLDER_WRITE_ONLY_OWN
    driver.remove_project("l7-folder")


# ── 12/13: a real web app, built in the box ─────────────────────────────────

def test_09_npm_workload_builds_and_exports(live):
    dl = os.path.join(live["scratch"], "tableaux.tar.gz")
    if os.path.exists(dl):
        os.unlink(dl)
    got = source_fetch.get_archive(TABLEAUX, allowed_hosts={"codeload.github.com"},
                                   max_bytes=20_000_000, dest_path=dl, service="code_download")
    say("tableaux download:", got["bytes"], got["sha256"][:16])
    assert got["sha256"].startswith(TABLEAUX_SHA_PREFIX)
    t0 = time.monotonic()
    put = driver.put_project("l7-tableaux", dl, disk_mb=2048)
    assert put.ok, put
    lock = cli_tools._box_read("l7-tableaux", "package-lock.json", locks.MAX_LOCK_BYTES)
    assert lock and locks.scan_npm_lock(lock) == []
    ci = driver.run(driver.BoxRequest(run_id=driver.new_run_id(), project="l7-tableaux",
                                      argv=locks.NPM_CI, what="box_install",
                                      allow_hosts=locks.NPM_HOSTS,
                                      limits={"wall_s": 900, "mem_mb": 3072, "cpu_s": 1800,
                                              "disk_mb": 2048}))
    say("npm ci:", ci.ok, ci.wall_s, ci.mem_peak_mb, len(ci.outside), ci.stderr_tail[-300:])
    assert ci.ok and ci.outside and all(o.get("host") == "registry.npmjs.org" for o in ci.outside)
    build = driver.run(driver.BoxRequest(run_id=driver.new_run_id(), project="l7-tableaux",
                                         argv=["npm", "run", "build"],
                                         limits={"wall_s": 600, "mem_mb": 3072, "disk_mb": 2048}))
    say("build:", build.ok, build.wall_s, build.stdout_tail[-300:])
    assert build.ok and build.outside == []
    dest = os.path.join(owner_folders.apps_root(), "l7-tableaux-proof", "dist")
    if os.path.isdir(os.path.dirname(dest)):
        from puffpai import remove_cli
        remove_cli.delete_tree(os.path.dirname(dest), allowed_roots=[owner_folders.apps_root()])
    files = driver.export("l7-tableaux", "client/dist", dest)
    total = sum(os.path.getsize(f) for f in files)
    say(f"export: {len(files)} files, {total} bytes, whole job {time.monotonic() - t0:.1f} s")
    assert any(f.endswith("index.html") for f in files) and total <= 200_000_000
    assert all(not owner_folders.is_link(f) for f in files)
    driver.remove_project("l7-tableaux")


# ── 14: a real command-line tool from PyPI ──────────────────────────────────

def test_10_csvkit_as_a_tool_the_bot_can_use(live):
    from puffpai.integrate import actions
    found = {"schema": "found/v1", "id": "pypi:csvkit", "source": "pypi", "part": "code-app",
             "name": "csvkit", "friendly": "csvkit", "url": "https://pypi.org/project/csvkit/",
             "package": {"registry": "pypi", "name": "csvkit", "version": "2.2.0", "bin": [],
                         "repo_name_matches": None}, "meaning": {"job": "j1"}}
    lines: list = []
    t0 = time.monotonic()
    got = cli_tools.add_found(found, item={"id": "apps_code:pypi:csvkit"}, say=lines.append)
    say("csvkit add:", got["status"], f"{time.monotonic() - t0:.1f} s", lines)
    assert got["status"] == "added", got
    iid = got["integration_id"]
    guests = os.path.join(live["profile"], "Documents", "Guests")
    os.makedirs(guests, exist_ok=True)
    with open(os.path.join(guests, "guests.csv"), "w", encoding="utf-8") as fh:
        fh.write("First Name,Last Name,Attending\nAda,Lee,Yes\nBo,Kim,No\nCy,Ng,Yes\n")
    device = ct.terminal_device_id()
    real, _why = owner_folders.check(guests, "ro")
    ticket = ct.mint("folder_give", ct.target_folder(iid, real, "ro"), "Give csvkit Guests.", device)
    assert cli_tools.grant_folder(iid, guests, "ro", ticket=ticket, device_id=device)["ok"]
    listed = [a for a in actions.all_actions() if a["integration"] == iid]
    say("actions:", sorted(a["id"].split("__")[1] for a in listed))
    stat = next(a for a in listed if a["id"].endswith("__csvstat"))
    assert stat["effect"] == "reads"
    res = actions.run(stat["id"], {"file": "guests.csv"}, {"device_id": device, "surface": "terminal"})
    say("csvstat:", res["ok"], res["line"], (res.get("data") or {}).get("output", "")[:400])
    assert res["ok"] and "First Name" in res["data"]["output"]
    bad = actions.run(stat["id"], {"file": "--version"}, {"device_id": device, "surface": "terminal"})
    assert not bad["ok"]
    assert locks.recorded(iid)["kind"] == "pip"


# ── design section 4: what an evil project could try ────────────────────────

def _tar(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
            else:
                info.size = len(data)
            tf.addfile(info, io.BytesIO(data or b""))
    return buf.getvalue()


def test_11_escapes_are_refused(live):
    scratch = live["scratch"]
    evil = os.path.join(scratch, "evil.tar")
    pkg = json.dumps({"name": "evilpkg", "version": "1.0.0",
                      "scripts": {"postinstall": "touch /work/PWNED; touch /tmp/PWNED"}}).encode()
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w:gz") as tf:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(pkg)
        tf.addfile(info, io.BytesIO(pkg))
    with open(evil, "wb") as fh:
        fh.write(_tar({"app/../../../etc/evil": b"x", "/abs-evil": b"x", "app/link": None,
                       "app/evilpkg-1.0.0.tgz": inner.getvalue(),
                       "app/package.json": b'{"private": true}'}))
    put = driver.put_project("l7-evil", evil, disk_mb=256)
    say("hostile tar:", put.ok, put.stderr_tail[-300:])
    assert "evil" not in root("ls /etc /srv/box /srv; ls /abs-evil 2>&1").replace("abs-evil: No", "")
    script = ("cd /work/app && npm install --ignore-scripts --offline --no-audit --no-fund"
              " ./evilpkg-1.0.0.tgz"
              " >/dev/null 2>&1; ls /work/PWNED 2>&1; ls /srv/box/projects 2>&1;"
              " ls /mnt/wsl 2>&1; ls /var/log/puffpai 2>&1; su -c true 2>&1 | head -1;"
              " cat /work/app/link 2>&1 | head -1")
    r = jailed("l7-evil", script, limits={"wall_s": 120})
    say("escapes:\n" + r.stdout_tail)
    assert r.stdout_tail.count("No such file") >= 4       # PWNED, /srv, /mnt/wsl, the log
    assert "pproxy" not in r.stdout_tail                  # the link reads the jail's passwd only
    assert "evilpkg" in root("ls /srv/box/projects/l7-evil/app/node_modules 2>&1")
    other = jailed("l7-evil", "ls /srv/box/projects/l7-tableaux 2>&1; ls /work/../ 2>&1")
    assert "No such file" in other.stdout_tail
    driver.remove_project("l7-evil")


# ── 17: the caller dies mid-run ──────────────────────────────────────────────

def test_12_cleanup_after_the_caller_is_killed(live, monkeypatch):
    monkeypatch.setattr(driver, "_runtime_hosts_allowed", lambda req: True)
    pics = os.path.join(live["profile"], "Pictures", "Party")
    os.makedirs(pics, exist_ok=True)
    req = driver.validate(driver.BoxRequest(
        run_id=driver.new_run_id(), project="l7-orphan", argv=["sleep", "300"],
        allow_hosts=["registry.npmjs.org"], what="box_traffic",
        folder={"windows_path": pics, "mode": "ro", "name": "files"}, limits={"wall_s": 600}))
    req.folder["windows_path"] = owner_folders.check(pics, "ro")[0]
    head = (json.dumps(req.wire()) + "\n").encode()
    proc = driver._popen([driver.wsl_exe(), "-d", DISTRO, "-u", "root", "--exec", driver.LAUNCHER],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         **driver._run_kwargs())
    proc.stdin.write(head)
    proc.stdin.flush()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and "sleep 300" not in root("ps -o args"):
        time.sleep(0.5)
    during = root(f"ls /sys/fs/cgroup/puffpai-box; ls /run/puffpai; grep -c /srv/box/owner /proc/mounts")
    say("during:", during.split())
    assert f"run-{req.run_id}" in during
    proc.kill()
    killed_at = time.monotonic()
    clean = False
    while time.monotonic() - killed_at < 6:
        after = root(f"ls /sys/fs/cgroup/puffpai-box; ls /run/puffpai;"
                     f" grep -c /srv/box/owner /proc/mounts; ps -o args | grep -c '^sleep 300'")
        if f"run-{req.run_id}" not in after and req.run_id not in after.split() \
                and after.split()[-2:] == ["0", "0"]:
            clean = True
            break
        time.sleep(0.3)
    took = time.monotonic() - killed_at
    say(f"cleanup after kill: {clean} in {took:.1f} s:", after.split())
    assert clean and took <= 4.5
    driver.remove_project("l7-orphan")


def test_13_projects_are_removed(live):
    jailed("l7-gone", "echo hi > /work/x")
    assert "l7-gone.img" in root("ls /srv/box/images")
    driver.remove_project("l7-gone")
    assert "l7-gone" not in root("ls /srv/box/images /srv/box/projects")


def test_99_her_distros_are_untouched(live):
    for project in ("l7-probe", "l7-proxy", "l7-tableaux", "l7-evil", "l7-orphan"):
        driver.remove_project(project)
    r = driver._wsl(["-l", "-v"], timeout=60)
    text = driver._text(r.stdout)
    say("wsl -l -v:\n" + text)
    names = {line.replace("*", " ").split()[0] for line in text.splitlines()[1:] if line.strip()}
    assert {"Ubuntu", "jetson-flash", "docker-desktop", DISTRO} <= names
