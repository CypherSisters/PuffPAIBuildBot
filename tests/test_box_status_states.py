"""The locked box's status, in every state, against a fake wsl.exe and a fake
Lxss registry.

Nothing here starts a real ``wsl.exe`` or reads the real registry: the
driver's one subprocess call (``_spawn``), its registry reader (``_lxss``) and
its ``wsl.exe`` finder are replaced by :class:`FakeWsl`, which also records
every command line so the guard tests can prove what was (and was never) run.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from puffpai.box import driver
from puffpai.box import wording as W

#: The WSL box is Windows only: off Windows box.driver answers before it
#: ever reaches WSL (Linux uses bubblewrap), so these have nothing to test.
wsl_box_only = pytest.mark.skipif(os.name != "nt", reason="the WSL box is Windows only")

WSL = r"C:\Windows\System32\wsl.exe"


def _utf16(text: str, *, bom: bool = False) -> bytes:
    raw = text.replace("\n", "\r\n").encode("utf-16-le")
    return (b"\xff\xfe" + raw) if bom else raw


class FakeWsl:
    """A stand-in wsl.exe and HKCU Lxss registry for the driver."""

    def __init__(self, monkeypatch, *, exe: str | None = WSL):
        self.calls: list[list[str]] = []
        self.registry: list[dict] = []
        self.markers: dict[str, dict] = {}
        self.launcher: dict[str, bool] = {}
        self.versions: dict[str, str] = {}
        self.version_out = "WSL version: 2.7.11.0\nKernel version: 6.18.33.2\n"
        self.selftest = {"schema": driver.SELFTEST_SCHEMA, "interop_refused": True,
                         "mnt_absent": True, "only_lo": True}
        self.list_result: tuple | None = None     # (rc, stdout bytes, stderr bytes)
        self.raise_on_list: BaseException | None = None
        self.bom = False
        monkeypatch.setattr(driver, "wsl_exe", lambda: exe)
        monkeypatch.setattr(driver, "_lxss", lambda: [dict(e) for e in self.registry])
        monkeypatch.setattr(driver, "_spawn", self.spawn)
        monkeypatch.setattr(driver, "_run_kwargs", lambda: {})

    # -- building the fake computer ---------------------------------------
    def add(self, name: str, base_path: str, *, version: str = "2",
            marker: bool | dict = True, launcher: bool = True) -> None:
        guid = "{%08x-0000-4000-8000-%012x}" % (len(self.registry) + 1, len(self.registry) + 1)
        self.registry.append({"guid": guid, "name": name, "base_path": base_path,
                              "version": 2 if version == "2" else 1})
        self.versions[name] = version
        if marker is True:
            self.markers[name] = {"distro": name, "created": "2026-09-27T00:00:00Z"}
        elif isinstance(marker, dict):
            self.markers[name] = marker
        self.launcher[name] = launcher

    def ran(self, *words: str) -> list[list[str]]:
        return [c for c in self.calls if all(w in c for w in words)]

    # -- the fake program ---------------------------------------------------
    def spawn(self, argv, *, timeout, input_bytes=None, **kwargs):
        self.calls.append([str(a) for a in argv])
        args = [str(a) for a in argv[1:]]

        def done(rc=0, out=b"", err=b""):
            return subprocess.CompletedProcess(argv, rc, out, err)

        if args == ["-l", "-v"]:
            if self.raise_on_list is not None:
                raise self.raise_on_list
            if self.list_result is not None:
                return done(*self.list_result)
            if not self.registry:
                return done(1, _utf16("Windows Subsystem for Linux has no installed "
                                      "distributions.\nError code: "
                                      "Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND\n"))
            lines = ["  NAME                   STATE           VERSION"]
            for i, e in enumerate(self.registry):
                star = "*" if i == 0 else " "
                lines.append(f"{star} {e['name']:<22} Stopped         "
                             f"{self.versions.get(e['name'], '2')}")
            return done(0, _utf16("\n".join(lines) + "\n", bom=self.bom))
        if args == ["--version"]:
            return done(0, _utf16(self.version_out))
        if len(args) >= 6 and args[0] == "-d" and args[2:5] == ["-u", "root", "--exec"]:
            name, rest = args[1], args[5:]
            if not any(e["name"] == name for e in self.registry):
                return done(1, b"", _utf16("There is no distribution with the supplied name.\n"))
            if rest == ["cat", driver.MARKER_PATH]:
                if name in self.markers:
                    return done(0, json.dumps(self.markers[name]).encode("utf-8"))
                return done(1, b"", b"cat: can't open '/etc/puffpai-box.json'")
            if rest == ["test", "-x", driver.LAUNCHER]:
                return done(0 if self.launcher.get(name) else 1)
            if rest[:1] == ["sha256sum"]:
                # The box holds exactly this BuildBot's own trusted programs.
                if not self.launcher.get(name):
                    return done(1)
                shas = driver.launcher_shas()
                return done(0, "".join(f"{shas[p]}  {p}\n" for p in rest[1:]).encode())
            if rest == [driver.LAUNCHER, "--self-test"]:
                return done(0, (json.dumps(self.selftest) + "\n").encode("utf-8"))
            return done(127)
        if args[:1] == ["--terminate"]:
            return done(0)
        if args[:1] == ["--unregister"]:
            self.registry = [e for e in self.registry if e["name"] != args[1]]
            return done(0)
        return done(1, _utf16("unknown command\n"))


@pytest.fixture
def box_env(tmp_path, monkeypatch):
    """Scratch roots: box root, install root, a profile without .wslconfig."""
    root = tmp_path / "box"
    install = tmp_path / "install"
    profile = tmp_path / "profile"
    for d in (root, install, profile):
        d.mkdir()
    (install / ".puffpai-install.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_BOX_ROOT", str(root))
    monkeypatch.setenv("PUFFPAI_CHECKOUT", str(install))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("PUFFPAI_BOX_DISTRO", raising=False)
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    return {"root": root, "install": install, "profile": profile, "tmp": tmp_path}


def _home(env, name):
    path = env["root"] / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "ext4.vhdx").write_bytes(b"\0" * (3 * 1024 * 1024))
    return str(path)


def _claim(env, name, *, install_root=None, cid=None):
    folder = env["root"] / "claims" / name
    folder.mkdir(parents=True, exist_ok=True)
    cid = cid or driver.install_id()
    body = {"install_root": str(install_root or env["install"]),
            "state_dir": "x", "created": "2026-09-27T00:00:00Z"}
    (folder / f"{cid}.json").write_text(json.dumps(body), encoding="utf-8")
    return folder / f"{cid}.json"


def _ready_box(env, fake, name="PuffPAI-sandbox"):
    fake.add(name, _home(env, name))
    _claim(env, name)
    return name


# ── naming ───────────────────────────────────────────────────────────────────

def test_distro_name_env_wins(box_env, monkeypatch):
    FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox-dev")
    assert driver.distro_name() == "PuffPAI-sandbox-dev"


def test_distro_name_default_when_free(box_env, monkeypatch):
    FakeWsl(monkeypatch)
    assert driver.distro_name() == "PuffPAI-sandbox"


def test_distro_name_default_when_claimed_by_this_install(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    assert driver.distro_name() == "PuffPAI-sandbox"


@wsl_box_only
def test_distro_name_suffixed_when_the_default_is_someone_elses(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", r"D:\elsewhere\PuffPAI-sandbox", marker=False)
    name = driver.distro_name()
    real = os.path.normcase(os.path.realpath(str(box_env["install"])))
    want = hashlib.sha256(real.encode("utf-8")).hexdigest()[:8]
    assert name == f"PuffPAI-sandbox-{want}"
    assert driver.NAME_RE.match(name)


# ── every state ──────────────────────────────────────────────────────────────

def test_no_wsl_when_wsl_exe_is_missing(box_env, monkeypatch):
    FakeWsl(monkeypatch, exe=None)
    st = driver.status()
    assert (st.state, st.offer, st.message) == ("no_wsl", "install_wsl", W.NO_WSL)


def test_no_wsl_when_the_feature_is_off(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.list_result = (1, _utf16("The Windows Subsystem for Linux is not installed. "
                                  "You can install by running 'wsl.exe --install'.\n"
                                  "Error code: Wsl/0x8007019e\n"), b"")
    assert driver.status().state == "no_wsl"


def test_wsl_off_when_it_does_not_answer(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.raise_on_list = subprocess.TimeoutExpired("wsl", 30)
    st = driver.status()
    assert (st.state, st.message) == ("wsl_off", W.WSL_OFF)


def test_wsl_off_names_the_virtualization_switch(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.list_result = (1, _utf16("Please enable the Virtual Machine Platform Windows "
                                  "feature and ensure virtualization is enabled in the "
                                  "BIOS.\nError code: Wsl/Service/0x80370102\n"), b"")
    st = driver.status()
    assert (st.state, st.message) == ("wsl_off", W.WSL_NO_VIRTUALIZATION)


def test_no_box_with_no_distros_at_all(box_env, monkeypatch):
    FakeWsl(monkeypatch)
    st = driver.status()
    assert (st.state, st.offer, st.distro) == ("no_box", "set_up_box", "PuffPAI-sandbox")


def test_ready_reads_utf16_listing_with_bom(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.bom = True
    fake.add("Ubuntu", r"C:\Users\x\AppData\Local\Packages\Ubuntu\LocalState")
    _ready_box(box_env, fake)
    st = driver.status()
    assert st.state == "ready", st
    assert st.message == W.READY
    # Her distro was listed, never entered.
    assert not fake.ran("-d", "Ubuntu")


@pytest.mark.parametrize("text,bom", [("  NAME STATE VERSION\n* Ubuntu Running 2\n", False),
                                      ("  NAME STATE VERSION\n* Ubuntu Running 2\n", True)])
def test_text_decodes_wsl_utf16(text, bom):
    assert "Ubuntu" in driver._text(_utf16(text, bom=bom))
    assert driver._text("plain utf-8 from inside".encode("utf-8")) == "plain utf-8 from inside"


def test_box_v1(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"), version="1")
    _claim(box_env, "PuffPAI-sandbox")
    st = driver.status()
    assert (st.state, st.offer) == ("box_v1", "repair_box")


def test_box_broken_without_the_launcher(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"), launcher=False)
    _claim(box_env, "PuffPAI-sandbox")
    st = driver.status()
    assert (st.state, st.offer) == ("box_broken", "repair_box")


def test_taken_when_the_base_path_is_outside_the_root(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", str(box_env["tmp"] / "not-the-root" / "PuffPAI-sandbox"))
    st = driver.status()
    assert st.state == "taken"
    assert st.message == W.TAKEN.format(distro="PuffPAI-sandbox")
    # Not ours by its disk place: never entered, not even to read a marker.
    assert not fake.ran("-d", "PuffPAI-sandbox")


def test_taken_when_our_folder_holds_a_distro_without_our_marker(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"), marker=False)
    _claim(box_env, "PuffPAI-sandbox")
    assert driver.status().state == "taken"


def test_taken_when_the_marker_names_another_distro(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"),
             marker={"distro": "PuffPAI-sandbox-other"})
    _claim(box_env, "PuffPAI-sandbox")
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    assert driver.status().state == "taken"


def test_a_name_that_is_not_a_box_name_is_taken(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("Ubuntu", _home(box_env, "Ubuntu"))
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "Ubuntu")
    st = driver.status()
    assert st.state == "taken"
    assert not fake.ran("-d", "Ubuntu")


def test_orphan_when_every_claim_is_from_a_deleted_install(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    _claim(box_env, "PuffPAI-sandbox", install_root=box_env["tmp"] / "gone", cid="a" * 16)
    st = driver.status()
    assert (st.state, st.offer) == ("orphan", "remove_orphan")
    assert st.message == W.ORPHAN.format(size="3 MB")
    assert "left over from a BuildBot that was deleted (3 MB). Remove it?" in st.message


@wsl_box_only
def test_orphan_offer_when_this_install_has_no_box_yet(box_env, monkeypatch):
    """A leftover default box: this install would get the suffixed name, and the
    status offers to clean the leftover up."""
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    st = driver.status()
    assert st.distro.startswith("PuffPAI-sandbox-")
    assert st.state == "orphan"
    assert st.orphans and st.orphans[0]["distro"] == "PuffPAI-sandbox"


def test_shared_box_is_offered_not_taken_over(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    other = box_env["tmp"] / "other-install"
    other.mkdir()
    _claim(box_env, "PuffPAI-sandbox", install_root=other, cid="b" * 16)
    st = driver.status()
    assert (st.state, st.offer, st.message) == ("no_box", "share_box", W.SHARED)
    # status never writes a claim: sharing needs the owner's yes.
    assert not driver._has_my_claim("PuffPAI-sandbox")


def _shared_default(box_env, fake, *, marker=True, base=None):
    fake.add("PuffPAI-sandbox", base or _home(box_env, "PuffPAI-sandbox"), marker=marker)
    other = box_env["tmp"] / "other-install"
    other.mkdir(exist_ok=True)
    _claim(box_env, "PuffPAI-sandbox", install_root=other, cid="b" * 16)


def test_second_install_reaches_the_shared_box_with_no_setting(box_env, monkeypatch):
    """SHARED is reachable without PUFFPAI_BOX_DISTRO.
    With no setting, a second BuildBot on the computer is offered the box the
    first one set up, and status still writes no claim."""
    fake = FakeWsl(monkeypatch)
    _shared_default(box_env, fake)
    assert driver.distro_name() == "PuffPAI-sandbox"
    st = driver.status()
    assert (st.distro, st.state, st.offer, st.message) == (
        "PuffPAI-sandbox", "no_box", "share_box", W.SHARED)
    assert not driver._has_my_claim("PuffPAI-sandbox")


def test_adopting_the_shared_box_adds_only_a_claim(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _shared_default(box_env, fake)
    lines = []
    st = driver._do_setup(driver.distro_name(), lines.append)
    assert driver._has_my_claim("PuffPAI-sandbox")
    assert st.state == "ready" and st.distro == "PuffPAI-sandbox"
    assert W.SETUP_SHARED in lines
    assert not fake.ran("--import") and not fake.ran("--unregister")
    assert not fake.ran("--terminate")


@wsl_box_only
def test_shared_default_without_the_marker_is_taken_not_shared(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _shared_default(box_env, fake, marker=False)
    assert driver.distro_name().startswith("PuffPAI-sandbox-")


@wsl_box_only
@pytest.mark.parametrize("default_is", ["free", "shared"])
def test_an_install_with_its_own_suffixed_box_keeps_it(box_env, monkeypatch, default_is):
    """An install whose box has the suffixed name lost
    track of it once the default name was free (or shareable)."""
    fake = FakeWsl(monkeypatch)
    mine = driver._suffixed_name()
    _ready_box(box_env, fake, name=mine)
    if default_is == "shared":
        _shared_default(box_env, fake)
    assert driver.distro_name() == mine
    assert driver.status().distro == mine


@wsl_box_only
def test_shared_default_with_its_disk_elsewhere_is_taken_not_shared(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _shared_default(box_env, fake, base=r"D:\elsewhere\PuffPAI-sandbox")
    assert driver.distro_name().startswith("PuffPAI-sandbox-")


@wsl_box_only
def test_shared_default_whose_other_claims_are_gone_is_not_adopted(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    _claim(box_env, "PuffPAI-sandbox", install_root=box_env["tmp"] / "gone", cid="c" * 16)
    assert driver.distro_name().startswith("PuffPAI-sandbox-")


@pytest.mark.parametrize("body", [
    "[wsl2]\nnetworkingMode=mirrored\n",
    "[wsl2]\r\nnetworkingMode = Mirrored # the new way\r\n",
    "[experimental]\nnetworkingMode=mirrored\n",
    "[wsl2]\nnetworkingMode=virtioproxy\n",
    "[experimental]\nhostAddressLoopback=true\n",
    "[wsl2]\nhostAddressLoopback = TRUE\n",
])
def test_not_nat(box_env, monkeypatch, body):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    (box_env["profile"] / ".wslconfig").write_text(body, encoding="utf-8")
    st = driver.status()
    assert (st.state, st.message) == ("not_nat", W.NOT_NAT)


def test_not_nat_reads_a_utf16_wslconfig(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    (box_env["profile"] / ".wslconfig").write_bytes(
        b"\xff\xfe" + "[wsl2]\r\nnetworkingMode=mirrored\r\n".encode("utf-16-le"))
    assert driver.status().state == "not_nat"


@pytest.mark.parametrize("body", [
    "", "[wsl2]\nmemory=8GB\n", "[wsl2]\nnetworkingMode=nat\n",
    "[wsl2]\n# networkingMode=mirrored\nhostAddressLoopback=false\n",
    "[other]\nnetworkingMode=mirrored\n",
])
def test_nat_settings_are_fine(box_env, monkeypatch, body):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    (box_env["profile"] / ".wslconfig").write_text(body, encoding="utf-8")
    assert driver.status().state == "ready"


def test_wslconfig_is_read_again_on_every_status(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    assert driver.status().state == "ready"
    (box_env["profile"] / ".wslconfig").write_text("[wsl2]\nnetworkingMode=mirrored\n",
                                                   encoding="utf-8")
    assert driver.status().state == "not_nat"


def test_a_changed_wsl_version_triggers_the_self_test(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    assert driver.status().state == "ready"
    assert len(fake.ran("--self-test")) == 1          # first time: no record yet
    hint = json.loads(open(driver._box_json_path(), encoding="utf-8").read())
    assert hint["distro"] == "PuffPAI-sandbox" and len(hint["wsl_version_sha"]) == 64
    assert driver.status().state == "ready"
    assert len(fake.ran("--self-test")) == 1          # same version: not again
    fake.version_out = "WSL version: 2.8.0.0\nKernel version: 6.19.1\n"
    assert driver.status().state == "ready"
    assert len(fake.ran("--self-test")) == 2          # changed: checked again


@pytest.mark.parametrize("broken", ["interop_refused", "mnt_absent", "only_lo"])
def test_a_failed_self_test_is_box_broken(box_env, monkeypatch, broken):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    fake.selftest[broken] = False
    st = driver.status()
    assert (st.state, st.offer) == ("box_broken", "repair_box")
    assert not os.path.exists(driver._box_json_path())


def test_self_test_rejects_a_truthy_answer_that_is_not_true(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)
    fake.selftest["only_lo"] = "yes"
    assert driver.status().state == "box_broken"


def test_unsupported_off_windows_and_linux(monkeypatch):
    monkeypatch.setattr(driver, "_platform", lambda: "other")
    st = driver.status()
    assert (st.state, st.message) == ("unsupported", W.UNSUPPORTED)


def test_linux_without_bubblewrap_says_so(monkeypatch):
    monkeypatch.setattr(driver, "_platform", lambda: "linux")
    monkeypatch.setattr(driver, "_bwrap", lambda: None)
    st = driver.status()
    assert (st.state, st.message) == ("no_box", W.LINUX_NO_BWRAP)


def test_every_state_is_one_of_the_pinned_states():
    assert set(driver.STATES) == {"ready", "no_wsl", "wsl_off", "no_box", "box_v1",
                                  "box_broken", "not_nat", "orphan", "taken",
                                  "unsupported"}


def test_status_never_raises(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    _ready_box(box_env, fake)

    def boom(*a, **k):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(driver, "_lxss", boom)
    st = driver.status()
    assert st.state == "box_broken"


def test_ingest_outside_accepts_only_box_lines(monkeypatch):
    seen = []
    from puffpai.security import egress_ledger
    monkeypatch.setattr(egress_ledger, "record",
                        lambda service, **f: seen.append((service, f["what"])) or True)
    n = driver.ingest_outside([
        {"service": "box_" + "a" * 24, "host": "registry.npmjs.org", "outcome": "done",
         "what": "box_install"},
        {"service": "weather", "host": "x.org", "outcome": "done", "what": "box_install"},
        {"service": "box_" + "b" * 24, "host": "x.org", "outcome": "done", "what": "forecast"},
        "not a line",
    ])
    # Install traffic is logged under the pinned service "box_install".
    assert n == 1 and seen == [("box_install", "box_install")]


def test_run_time_lines_fall_back_when_the_log_drops_the_switch_id(monkeypatch):
    """The outside log drops service names that look like a pasted key, and
    a box switch id is 24 hex: then the line is kept under "box_traffic"."""
    seen = []
    from puffpai.security import egress_ledger
    monkeypatch.setattr(egress_ledger, "record",
                        lambda service, **f: seen.append(service) or service == "box_traffic")
    n = driver.ingest_outside([{"service": "box_" + "c" * 24, "host": "api.example.com",
                                "outcome": "refused", "code": "not_on_list",
                                "what": "box_traffic"}])
    assert n == 1 and seen == ["box_" + "c" * 24, "box_traffic"]
