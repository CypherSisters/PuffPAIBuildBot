#!/usr/bin/python3 -I
"""PuffPAI locked box launcher. Runs as ROOT inside the box (installed as
/opt/puffpai/box-run by setup_box.sh). Ported from the proven prototype
(design/sandbox/box/box_run.py, proofs 07-19).

Invoked by the Windows backend (puffpai.box.driver) as:
    wsl.exe -d <box> -u root --exec /opt/puffpai/box-run
Protocol: stdin line 1 = one JSON request (<= 64 KB). Further stdin bytes go
to the program's stdin when request.stdin == "pass" (a project tarball, an
input file). stdout = one JSON result line (stdout_mode "capture"), or the
program's raw stdout followed by the result on stderr as
"PUFFBOX-RESULT <json>" (stdout_mode "raw", used to stream a built bundle out).

Other modes (argv only, no request):
    box-run --self-test              the safety check after WSL changed
    box-run --remove-project <name>  drop a project's disk image

Trusted code: this file parses the request strictly and never passes request
text to a shell. Outside code only ever runs as uid 61000 inside bubblewrap,
built by /opt/puffpai/jail.py (the same file as puffpai.box.jail).
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

OPT = "/opt/puffpai"
sys.dont_write_bytecode = True     # nothing written next to the trusted files
sys.path.insert(0, OPT)
import jail  # noqa: E402  (the one audited builder, copied in at setup)

RUNNER, PROXY = 61000, 61001
PROJ_IMG = "/srv/box/images"
PROJ_MNT = "/srv/box/projects"
OWNER_MNT = "/srv/box/owner"
RUNDIR = "/run/puffpai"
LOGDIR = "/var/log/puffpai"
CG_ROOT = "/sys/fs/cgroup/puffpai-box"
DENY_FILE = "/etc/puffpai/deny_windows.json"
DENY_DIR = "/etc/puffpai/deny.d"
SELFTEST_SCHEMA = "puffpai_box_selftest/v1"

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
HOST_RE = re.compile(r"^(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)"
                     r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z][a-z0-9-]*[a-z0-9]\Z")
SERVICE_RE = re.compile(r"^box_[0-9a-f]{24}\Z")
WHAT = {"box_install", "box_traffic"}
WINPATH_RE = re.compile(r'^[A-Za-z]:\\[^<>:"/|?*\x00-\x1f]+\Z')
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
LIMIT_MAX = {"cpu_s": 3600, "mem_mb": 8192, "wall_s": 3600, "pids": 1024,
             "fsize_mb": 4096, "disk_mb": 20480}
LIMIT_DEF = {"cpu_s": 600, "mem_mb": 2048, "wall_s": 900, "pids": 256,
             "fsize_mb": 512, "disk_mb": 4096}
FIELDS = {"run_id", "project", "argv", "allow_hosts", "service", "what",
          "folder", "limits", "stdin", "stdout_mode", "env"}
TAIL = 4096


class Refused(Exception):
    pass


def validate(req):
    if not isinstance(req, dict):
        raise Refused("request must be an object")
    if set(req) - FIELDS:
        raise Refused("unknown fields")
    for k in ("run_id", "project"):
        if not isinstance(req.get(k), str) or not ID_RE.match(req[k]):
            raise Refused(f"bad {k}")
    argv = req.get("argv")
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 64 or
            not all(isinstance(a, str) and len(a) <= 4096 and "\x00" not in a for a in argv)):
        raise Refused("bad argv")
    hosts = req.get("allow_hosts", [])
    if not isinstance(hosts, list) or len(hosts) > 32:
        raise Refused("bad allow_hosts")
    for h in hosts:
        if not isinstance(h, str):
            raise Refused("bad allow_hosts entry")
        name, _, port = h.partition(":")
        if not HOST_RE.match(name) or port not in ("", "443"):
            raise Refused("bad allow_hosts entry")   # 443 only (fail closed)
    if not isinstance(req.get("service"), str) or not SERVICE_RE.match(req["service"]):
        raise Refused("bad service")
    if req.get("what", "box_traffic") not in WHAT:
        raise Refused("bad what")
    if req.get("stdin", "none") not in ("none", "pass"):
        raise Refused("bad stdin")
    if req.get("stdout_mode", "capture") not in ("capture", "raw"):
        raise Refused("bad stdout_mode")
    env = req.get("env", {})
    if (not isinstance(env, dict) or len(env) > 32 or not all(
            isinstance(k, str) and ENV_RE.match(k)
            and isinstance(v, str) and len(v) <= 4096 and "\x00" not in v
            for k, v in env.items())):
        raise Refused("bad env")
    lim = dict(LIMIT_DEF)
    limits = req.get("limits") or {}
    if not isinstance(limits, dict):
        raise Refused("bad limits")
    for k, v in limits.items():
        if k not in LIMIT_MAX or not isinstance(v, int) or isinstance(v, bool) \
                or not 1 <= v <= LIMIT_MAX[k]:
            raise Refused("bad limits")
        lim[k] = v
    req["_limits"] = lim
    folder = req.get("folder")
    if folder is not None:
        if (not isinstance(folder, dict) or set(folder) - {"windows_path", "mode", "name"}
                or folder.get("mode") not in ("ro", "rw")
                or not ID_RE.match(str(folder.get("name", "files")))):
            raise Refused("bad folder")
        if req.get("what", "box_traffic") == "box_install":
            raise Refused("no folder while installing")
        check_folder(str(folder.get("windows_path", "")))
    return req


def norm_win(p):
    return p.replace("/", "\\").rstrip("\\").lower()


def _root_only(path):
    """A deny list file or folder only root may change (else it proves nothing)."""
    st = os.lstat(path)
    if st.st_uid != 0 or st.st_mode & 0o022 or os.path.islink(path):
        raise Refused("deny list unreadable")


def deny_list():
    """Every deny-listed Windows path: the fixed list written at setup
    (DENY_FILE: the system places plus the private folders of the BuildBot
    that set the box up) and one file per BuildBot that uses this box
    (DENY_DIR/<claim id>.json: its vault and inbox, state, data, apps, box and
    install folders), rewritten by that BuildBot before every run with an
    owner folder. Anything unreadable, not root's, or not a list of strings
    refuses (fail closed). A request can never take an entry out."""
    out = []
    files = [DENY_FILE]
    try:
        if os.path.lexists(DENY_DIR):
            _root_only(DENY_DIR)
            files += [os.path.join(DENY_DIR, n) for n in sorted(os.listdir(DENY_DIR))
                      if n.endswith(".json")]
        for f in files:
            _root_only(f)
            with open(f, encoding="utf-8") as fh:
                got = json.load(fh)
            if not isinstance(got, list) or not all(isinstance(d, str) and d for d in got):
                raise Refused("deny list unreadable")
            out += [norm_win(d) for d in got]
    except Refused:
        raise
    except Exception:
        raise Refused("deny list unreadable")              # fail closed
    if not out:
        raise Refused("deny list unreadable")
    return out


def check_folder(path):
    """Refuse the vault and every other deny-listed place, at, inside or ABOVE
    it. The Windows side already resolved links and applied the same list;
    this is the second check, made here against the lists kept in the box
    (:func:`deny_list`), which a request cannot change."""
    if not WINPATH_RE.match(path) or "\\..\\" in path + "\\" or "\\.\\" in path + "\\":
        raise Refused("folder path not allowed")
    p = norm_win(path)
    if p.count("\\") < 2:                                  # C:\ or C:\Users
        raise Refused("folder path not allowed")
    deny = deny_list()
    for d in deny:
        if p == d or p.startswith(d + "\\") or d.startswith(p + "\\"):
            raise Refused("folder path not allowed")


def cg_write(path, value):
    with open(path, "w") as fh:
        fh.write(value)


def cg_read(path, default=None):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def setup_cgroup(run_id, lim):
    os.makedirs(CG_ROOT, exist_ok=True)
    cg_write(os.path.join(CG_ROOT, "cgroup.subtree_control"), "+cpu +memory +pids")
    cg = os.path.join(CG_ROOT, "run-" + run_id)
    os.makedirs(cg, exist_ok=False)
    cg_write(os.path.join(cg, "memory.max"), str(lim["mem_mb"] * 1024 * 1024))
    cg_write(os.path.join(cg, "memory.swap.max"), "0")
    cg_write(os.path.join(cg, "pids.max"), str(lim["pids"]))
    cg_write(os.path.join(cg, "cpu.max"), "200000 100000")   # at most 2 CPUs
    return cg


def ensure_project(name, disk_mb):
    """Each project lives on its own ext4 image: a hard disk cap, so a runaway
    write cannot grow the box's disk (and the Windows C: drive) without end."""
    os.makedirs(PROJ_IMG, mode=0o700, exist_ok=True)
    img = os.path.join(PROJ_IMG, name + ".img")
    mnt = os.path.join(PROJ_MNT, name)
    if not os.path.exists(img):
        with open(img, "wb") as fh:
            fh.truncate(disk_mb * 1024 * 1024)
        subprocess.run(["mkfs.ext4", "-q", "-m", "0", "-E", f"root_owner={RUNNER}:{RUNNER}", img],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.makedirs(mnt, exist_ok=True)
    if not os.path.ismount(mnt):
        subprocess.run(["mount", "-o", "loop,nosuid,nodev", img, mnt], check=True)
    return mnt


def remove_project(name):
    if not ID_RE.match(name or ""):
        raise Refused("bad project")
    mnt = os.path.join(PROJ_MNT, name)
    if os.path.ismount(mnt):
        subprocess.run(["umount", mnt], check=False)
        if os.path.ismount(mnt):
            subprocess.run(["umount", "-l", mnt], check=False)
    try:
        os.rmdir(mnt)
    except OSError:
        pass
    img = os.path.join(PROJ_IMG, name + ".img")
    try:
        os.unlink(img)
    except FileNotFoundError:
        pass


def mount_folder(run_id, folder):
    tgt = os.path.join(OWNER_MNT, run_id)
    os.makedirs(tgt, mode=0o700, exist_ok=True)
    subprocess.run(["mount", "-t", "drvfs", folder["windows_path"], tgt, "-o",
                    f"noexec,nosuid,nodev,uid={RUNNER},gid={RUNNER},umask=077"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # drvfs IGNORES "-o ro" (measured, 11a): enforce read-only with a bind remount.
    flags = "remount,bind,nosuid,nodev,noexec" + (",ro" if folder["mode"] == "ro" else "")
    subprocess.run(["mount", "-o", flags, tgt], check=True)
    return tgt


def symlinks_under(root):
    out = set()
    for d, dirs, files in os.walk(root, followlinks=False):
        for n in dirs + files:
            p = os.path.join(d, n)
            if os.path.islink(p):
                out.add(p)
    return out


def start_proxy(run_id, req):
    rd = os.path.join(RUNDIR, run_id)
    os.makedirs(rd, exist_ok=False)
    os.chown(rd, PROXY, RUNNER)
    os.chmod(rd, 0o750)
    os.makedirs(LOGDIR, mode=0o750, exist_ok=True)
    os.chown(LOGDIR, 0, PROXY)          # pproxy may traverse, runner may not
    os.chmod(LOGDIR, 0o750)
    log = os.path.join(LOGDIR, run_id + ".outside.jsonl")
    open(log, "a").close()
    os.chown(log, PROXY, PROXY)
    os.chmod(log, 0o600)
    sock = os.path.join(rd, "egress.sock")
    hosts = ",".join(h if ":" in h else h + ":443" for h in req.get("allow_hosts", []))
    # The proxy is trusted code but parses bytes from the jail, and it has the
    # network. Jail it too: network shared, but no /mnt/wsl (Docker Desktop's
    # world-writable backend.sock lives there), no /srv, no other runs; only
    # its own socket dir, its log file, and the name-lookup files.
    sfd = os.open(os.path.join(OPT, "box.bpf"), os.O_RDONLY)
    p = subprocess.Popen(
        ["setpriv", f"--reuid={PROXY}", f"--regid={PROXY}", "--clear-groups",
         "--no-new-privs", "--inh-caps=-all",
         "bwrap", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
         "--unshare-cgroup", "--disable-userns", "--die-with-parent", "--new-session",
         "--clearenv", "--cap-drop", "ALL",
         "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/bin", "/bin",
         "--tmpfs", "/usr/lib/wsl",
         "--ro-bind", os.path.realpath("/etc/resolv.conf"), "/etc/resolv.conf",
         "--ro-bind", "/etc/hosts", "/etc/hosts",
         "--ro-bind", os.path.join(OPT, "egress_proxy.py"), "/opt/egress_proxy.py",
         "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
         "--bind", rd, "/run/px", "--bind", log, "/log/outside.jsonl",
         "--remount-ro", "/", "--seccomp", str(sfd), "--",
         "/usr/bin/python3", "-I", "/opt/egress_proxy.py",
         "--socket", "/run/px/egress.sock", "--allow", hosts, "--log", "/log/outside.jsonl",
         "--service", req["service"], "--what", req.get("what", "box_traffic")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        pass_fds=(sfd,), env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    os.close(sfd)
    if p.stdout.readline().strip() != b"ready":
        p.kill()
        raise Refused("proxy did not start")
    os.chown(sock, PROXY, RUNNER)
    os.chmod(sock, 0o660)
    return p, sock, log


def run_argv(req, proj, folder_mnt, sock):
    lim = req["_limits"]
    folder = None
    if folder_mnt:
        folder = {"path": folder_mnt, "mode": req["folder"]["mode"],
                  "name": req["folder"].get("name", "files")}
    prefix = ["setpriv", f"--reuid={RUNNER}", f"--regid={RUNNER}", "--clear-groups",
              "--no-new-privs", "--inh-caps=-all",
              "prlimit", f"--cpu={lim['cpu_s']}", "--core=0", "--nofile=4096",
              f"--fsize={lim['fsize_mb'] * 1024 * 1024}", "--"]
    return prefix + jail.bwrap_argv(proj, folder=folder, egress_sock=sock,
                                    env=req.get("env") or None) + req["argv"]


def tail(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - TAIL))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


class Hangup(Exception):
    pass


def _on_signal(signum, frame):
    raise Hangup(signum)


LOCKDIR = os.path.join(RUNDIR, "locks")


def hold_run_lock(run_id):
    """Held for the whole run; a run whose lock can be taken is dead."""
    os.makedirs(LOCKDIR, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(LOCKDIR, run_id), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refused("run id in use")
    return fd


def sweep():
    """Clean up after runs whose launcher died without its finally block
    (for example the Windows side was killed): stale owner-folder mounts
    first, then cgroups, run dirs and output files (proof 17)."""
    if not os.path.isdir(LOCKDIR):
        return
    for rid in os.listdir(LOCKDIR):
        fd = os.open(os.path.join(LOCKDIR, rid), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue                      # live run
        mnt = os.path.join(OWNER_MNT, rid)
        if os.path.ismount(mnt):
            subprocess.run(["umount", "-l", mnt])
        for path in (mnt, os.path.join(CG_ROOT, "run-" + rid)):
            if path.startswith(CG_ROOT) and os.path.isdir(path):
                try:
                    cg_write(os.path.join(path, "cgroup.kill"), "1")
                except OSError:
                    pass
                time.sleep(0.1)
            try:
                os.rmdir(path)
            except OSError:
                pass
        shutil.rmtree(os.path.join(RUNDIR, rid), ignore_errors=True)
        for ext in (".out", ".err"):
            try:
                os.unlink(os.path.join(RUNDIR, "out", rid + ext))
            except OSError:
                pass
        os.unlink(os.path.join(LOCKDIR, rid))
        os.close(fd)


def windows_drive_mounted(mounts_text=None):
    """True when anything but WSL's own /mnt/wsl and /mnt/wslg is mounted
    under /mnt (a Windows drive): the lock (/etc/wsl.conf) is not in force."""
    if mounts_text is None:
        try:
            with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
                mounts_text = fh.read()
        except OSError:
            return True                                    # cannot tell: refuse
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("/mnt/") and not parts[1].startswith("/mnt/wsl"):
            return True
    return False


def seccomp_fd():
    """Open box.bpf onto jail.SECCOMP_FD (the number the jail argv names)."""
    fd = os.open(os.path.join(OPT, "box.bpf"), os.O_RDONLY)
    if fd != jail.SECCOMP_FD:
        os.dup2(fd, jail.SECCOMP_FD)
        os.close(fd)
    return jail.SECCOMP_FD


def execute(req, stdin_fd, out_stream):
    """Run one validated request. Returns the result dict (never raises)."""
    result = {"schema": "puffpai_box_result/v1", "ok": False}
    cg = proxy = folder_mnt = None
    links_before = set()
    rid = None
    try:
        run_id = req["run_id"]
        if windows_drive_mounted():
            raise Refused("the box is not locked")
        sweep()
        hold_run_lock(run_id)
        rid = run_id
        result["run_id"] = run_id
        lim = req["_limits"]
        cg = setup_cgroup(run_id, lim)
        proj = ensure_project(req["project"], lim["disk_mb"])
        if req.get("folder"):
            folder_mnt = mount_folder(run_id, req["folder"])
            links_before = symlinks_under(folder_mnt)
        sock = log = None
        if req.get("allow_hosts"):
            proxy, sock, log = start_proxy(run_id, req)
        sfd = seccomp_fd()
        os.makedirs(os.path.join(RUNDIR, "out"), exist_ok=True)
        out_path = os.path.join(RUNDIR, "out", run_id + ".out")
        err_path = os.path.join(RUNDIR, "out", run_id + ".err")
        raw = req.get("stdout_mode") == "raw"
        out_f = out_stream if raw else open(out_path, "wb")
        err_f = open(err_path, "wb")

        def enter_cgroup():
            cg_write(os.path.join(cg, "cgroup.procs"), str(os.getpid()))
        t0 = time.monotonic()
        child = subprocess.Popen(
            run_argv(req, proj, folder_mnt, sock),
            stdin=stdin_fd if req.get("stdin") == "pass" else subprocess.DEVNULL,
            stdout=out_f, stderr=err_f, pass_fds=(sfd,), preexec_fn=enter_cgroup,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        timed_out = False
        try:
            rc = child.wait(timeout=lim["wall_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            cg_write(os.path.join(cg, "cgroup.kill"), "1")
            rc = child.wait()
        wall = time.monotonic() - t0
        if not raw:
            out_f.close()
        err_f.close()
        events = dict(line.split() for line in (cg_read(os.path.join(cg, "memory.events"), "")
                                                or "").splitlines() if line)
        cpu = dict(line.split() for line in (cg_read(os.path.join(cg, "cpu.stat"), "")
                                             or "").splitlines() if line)
        st = os.statvfs(proj)
        result.update({
            "ok": rc == 0 and not timed_out, "exit_code": rc, "timed_out": timed_out,
            "oom_killed": int(events.get("oom_kill", 0)) > 0,
            "wall_s": round(wall, 1), "cpu_s": round(int(cpu.get("usage_usec", 0)) / 1e6, 1),
            "mem_peak_mb": round(int(cg_read(os.path.join(cg, "memory.peak"), "0") or 0) / 2**20, 1),
            "pids_peak": int(cg_read(os.path.join(cg, "pids.peak"), "0") or 0),
            "project_disk_used_mb": round((st.f_blocks - st.f_bfree) * st.f_frsize / 2**20, 1),
            "stdout_tail": "" if raw else tail(out_path), "stderr_tail": tail(err_path),
        })
        if log:
            proxy.terminate()
            proxy.wait(5)
            proxy = None
            with open(log, encoding="utf-8") as fh:
                result["outside"] = [json.loads(line) for line in fh if line.strip()][-500:]
    except Refused as exc:
        result["refused"] = str(exc)
    except Hangup:
        result["error"] = "stopped: the caller went away"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        if proxy:
            proxy.kill()
        if cg and os.path.isdir(cg):
            try:
                cg_write(os.path.join(cg, "cgroup.kill"), "1")
                time.sleep(0.2)
                os.rmdir(cg)
            except OSError:
                pass
        if folder_mnt:
            if (req.get("folder") or {}).get("mode") == "rw":
                # A link the program planted could later lead a Windows reader
                # elsewhere: remove every NEW symlink before the folder is handed back.
                new = symlinks_under(folder_mnt) - links_before
                for p in new:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                result["removed_new_links"] = len(new)
            subprocess.run(["umount", "-l", folder_mnt])
            try:
                os.rmdir(folder_mnt)
            except OSError:
                pass
        if rid:
            shutil.rmtree(os.path.join(RUNDIR, rid), ignore_errors=True)
            for ext in (".out", ".err"):
                try:
                    os.unlink(os.path.join(RUNDIR, "out", rid + ext))
                except OSError:
                    pass
            try:
                os.unlink(os.path.join(LOCKDIR, rid))
            except OSError:
                pass
            try:
                os.unlink(os.path.join(LOGDIR, rid + ".outside.jsonl"))
            except OSError:
                pass
    return result


SELFTEST_SCRIPT = r"""
mnt=absent; [ -e /mnt ] && mnt=present
runwsl=absent; [ -e /run/WSL ] && runwsl=present
ifs=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | sort | tr '\n' ',')
printf 'MZ\000\000\000\000\000\000' > /tmp/probe.exe && chmod +x /tmp/probe.exe
/tmp/probe.exe >/dev/null 2>&1; pe=$?
echo "mnt=$mnt ifs=$ifs pe=$pe interop=${WSL_INTEROP:-none} runwsl=$runwsl"
"""


def self_test():
    """Inside a real run: a Windows program is refused, /mnt is absent and
    only lo exists. Prints one JSON line."""
    req = validate({"run_id": "selftest-" + os.urandom(4).hex(), "project": "selftest",
                    "argv": ["/bin/sh", "-c", SELFTEST_SCRIPT],
                    "service": "box_" + "0" * 24, "what": "box_traffic",
                    "limits": {"disk_mb": 64, "wall_s": 60, "mem_mb": 256}})
    res = execute(req, None, None)
    line = (res.get("stdout_tail") or "").strip().splitlines()
    fields = dict(part.split("=", 1) for part in (line[-1].split() if line else []) if "=" in part)
    remove_project("selftest")
    out = {"schema": SELFTEST_SCHEMA, "ran": bool(res.get("ok")),
           "interop_refused": fields.get("pe", "0") != "0" and fields.get("interop") == "none"
           and fields.get("runwsl") == "absent",
           "mnt_absent": fields.get("mnt") == "absent",
           "only_lo": fields.get("ifs") == "lo,"}
    if not res.get("ok"):
        out.update({"interop_refused": False, "mnt_absent": False, "only_lo": False})
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


def main():
    # Read the request line straight from fd 0, one byte at a time, so no
    # buffered reader swallows the start of a streamed payload behind it.
    buf = bytearray()
    while len(buf) < 65536:
        ch = os.read(0, 1)
        if not ch or ch == b"\n":
            break
        buf += ch
    head = bytes(buf)
    raw = False
    try:
        req = json.loads(head)
        raw = isinstance(req, dict) and req.get("stdout_mode") == "raw"
        req = validate(req)
        result = execute(req, 0, sys.stdout.buffer)
    except Refused as exc:
        result = {"schema": "puffpai_box_result/v1", "ok": False, "refused": str(exc)}
    except Exception as exc:
        result = {"schema": "puffpai_box_result/v1", "ok": False,
                  "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    line = json.dumps(result, sort_keys=True)
    if raw:
        sys.stdout.buffer.flush()
        sys.stderr.write("PUFFBOX-RESULT " + line + "\n")
    else:
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    for _sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(_sig, _on_signal)
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    if len(sys.argv) == 3 and sys.argv[1] == "--remove-project":
        try:
            remove_project(sys.argv[2])
        except Refused as exc:
            sys.stdout.write(json.dumps({"ok": False, "refused": str(exc)}) + "\n")
            sys.exit(2)
        sys.stdout.write(json.dumps({"ok": True}) + "\n")
        sys.exit(0)
    if len(sys.argv) > 1:
        sys.exit(2)
    sys.exit(main())
