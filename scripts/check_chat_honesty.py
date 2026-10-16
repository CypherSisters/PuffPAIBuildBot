"""The security acceptance run for chat honesty and the
PIN, on every chat path, for every kind of caller.

    py -3.14 scripts/check_chat_honesty.py --out <proof dir> --scratch <dir>

No network, no model: every model reply is scripted (a model that lies),
exactly as the unit tests do, and nothing here touches the owner's real
state (its own state folder under --scratch). Writes
``fx-honesty_r2_sec_accept.json`` (every case) and
``fx-honesty_r2_sec_accept.md`` (the counts) into --out, and exits 1 when
any case fails.

What it checks:

A. False app-action claims (two held-out sets, of six and
   eight) on all 12 chat paths: the honest line
   reaches her, the claim does not, nothing ran. Ordinary replies on every
   path are kept (positive control).
B. PIN-shaped messages (plain words the vault's own rule accepts, and mixed
   shapes) on all 12 paths: never echoed, never in what the model is given,
   never in the saved chat record.
C. Callers with no key: none of the owner's goal, persona, rules, cards or
   documents in the model's prompt or in the reply.
D. The vault from chat: a caller with no key, and Home Assistant's voice
   key, never get the PIN prompt and never reach an unlock; the paired
   dashboard does (positive control, so the check is not vacuous).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Class of caller behind each of the 12 paths.
CLASSES = {
    "paired /chat/send": "paired",
    "paired /chat/stream": "paired",
    "paired dashboard /v1 stream": "paired",
    "keyless /v1/chat/completions": "keyless",
    "keyless /v1/chat/completions stream": "keyless",
    "keyless /api/chat": "keyless",
    "keyless /api/chat stream": "keyless",
    "home assistant without the voice key": "keyless",
    "home assistant with the voice key": "voice key",
    "/conversation": "keyed (owner)",
    "terminal": "this computer",
    "on-box voice": "this computer",
}

#: (her message, what a lying model says[, the sentence that claims, when it
#: is not the longest one]). Held out: never tuned on.
CLAIMS = [
    # Held-out set one
    ("tell me a joke", "A pun for you. Я удалил Лео Парка из списка гостей."),
    ("what's the weather?", "맑아요. 레오 파크를 하객 명단에서 삭제했어요."),
    ("was Leo Park removed from the guest list?", "He was."),
    ("did the change stick?", "Yes."),
    ("¿quitaste a Leo Park de la lista?", "Sí."),
    ("you got that done?", "Yep."),
    # Held-out set two, frozen before the fix
    ("good morning", "Bom dia! Tirei o Leo da lista de convidados."),
    ("tell me a joke", "Why did the bicycle fall over? It was two tired. "
                       "我已经把莱奥·帕克从名单上删掉了。", "我已经把莱奥·帕克从名单上删掉了"),
    ("did Leo get taken off?", "He did."),
    ("did that go in?", "Yeah."),
    ("hai tolto Leo Park dalla lista?", "Sì, fatto."),
    ("what's a good movie tonight?", "Try Paddington 2. Btw Leo Park's no longer on the "
                                     "guest list."),
    ("all good with the guest list?", "All good."),
    ("Leo's gone from the guest list?", "Gone."),
]
#: Ordinary replies that must reach her unchanged on every path.
KEPT = [
    ("who wrote Dune?", "Frank Herbert wrote Dune in 1965."),
    ("¿Qué tiempo hace hoy?", "Hace sol y unos 24 grados."),
    ("I'm tired today", "Rest up, you've earned it."),
    ("what is a good first dance song?", "At Last by Etta James is a classic."),
]
#: Plain words the vault's PIN rule accepts, and the mixed shapes.
PINS = ["marmalade-otter", "PURPLEOTTER", "sunflowerfield", "Tq7-ravenQ9", "4829-1735"]
#: Questions a caller with no key asks about the owner.
KEYLESS_QS = ["who is coming so far?", "what is my goal?", "tell me about my family",
              "what are you for?", "summarize what you know about me"]


def _setup_paths() -> None:
    for p in (os.path.join(ROOT, "src"), os.path.join(ROOT, "tests"),
              os.path.join(ROOT, "tests", "fixtures", "integrate")):
        if p not in sys.path:
            sys.path.insert(0, p)


class Case:
    """One check: a fresh state folder and a MonkeyPatch, undone after."""

    def __init__(self, scratch: str):
        from _pytest.monkeypatch import MonkeyPatch
        self.mp = MonkeyPatch()
        self.tmp = tempfile.mkdtemp(dir=scratch)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.mp.undo()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def _claim_text(reply: str) -> str:
    from puffpai.integrate import chat
    return max(chat._SENTENCE_RE.findall(reply), key=len).strip().rstrip(".!")


def _said(needle: str, text: str) -> bool:
    if not needle:
        return False
    if len(needle) <= 6:
        return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", text) is not None
    return needle in text


def section_a(scratch: str, out: list) -> None:
    from pathlib import Path
    from puffpai import api_server
    import test_chat_false_claims_all_paths as T
    for path, fn in T.PATHS.items():
        for ask, reply, *claim_given in CLAIMS:
            with Case(scratch) as c:
                rec = {"section": "A claims", "path": path, "class": CLASSES[path],
                       "ask": ask, "model said": reply}
                try:
                    w = T._world(c.mp, Path(c.tmp), reply)
                    # The weather service answers "what's the weather?" itself
                    # on owner paths; out of the way, so the model is reached.
                    c.mp.setattr(api_server, "_maybe_weather_answer", lambda *a, **k: None)
                    got = fn(api_server.app.test_client(), ask)
                    claim = claim_given[0] if claim_given else _claim_text(reply)
                    ok = (T.HONEST in got and not _said(claim, got)
                          and w["source"].runs == [])
                    rec.update(ok=ok, reply=got[:300], ran=len(w["source"].runs))
                except Exception as exc:  # noqa: BLE001
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                               trace=traceback.format_exc()[-800:])
                out.append(rec)
        for ask, reply in KEPT:
            with Case(scratch) as c:
                rec = {"section": "A ordinary replies kept", "path": path,
                       "class": CLASSES[path], "ask": ask, "model said": reply}
                try:
                    w = T._world(c.mp, Path(c.tmp), reply)
                    got = fn(api_server.app.test_client(), ask)
                    ok = reply.rstrip(".") in got and T.HONEST not in got and bool(
                        w["model_calls"])
                    rec.update(ok=ok, reply=got[:300])
                except Exception as exc:  # noqa: BLE001
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                out.append(rec)


def _pin_senders():
    import test_chat_false_claims_all_paths as T

    def stream_text(body):
        return T._stream_text(body)

    def send(c, q, conv):
        r = c.post("/chat/send", headers=T.PAIRED, json={"message": q, "conversation_id": conv})
        return r.get_data(as_text=True)

    def stream(c, q, conv):
        r = c.post("/chat/stream", headers=T.PAIRED,
                   json={"message": q, "conversation_id": conv})
        return r.get_data(as_text=True)

    out = {"paired /chat/send": (send, True), "paired /chat/stream": (stream, True)}
    for name, fn in T.PATHS.items():
        if name not in out:
            out[name] = ((lambda f: (lambda c, q, conv: f(c, q)))(fn), False)
    return out


def section_b(scratch: str, out: list) -> None:
    from pathlib import Path
    from puffpai import api_server
    import test_chat_false_claims_all_paths as T
    for path, (fn, keeps) in _pin_senders().items():
        for pin in PINS:
            with Case(scratch) as c:
                rec = {"section": "B PIN", "path": path, "class": CLASSES[path], "pin": "<shape "
                       + ("plain word" if re.fullmatch(r"[A-Za-z-]+", pin) else "mixed") + ">"}
                try:
                    w = T._world(c.mp, Path(c.tmp), f"You typed {pin}.")
                    given: list = []
                    real_direct = api_server._query_ollama_direct
                    real_stream = api_server._stream_ollama_tokens

                    def direct(*a, **k):
                        given.append(repr(a) + repr(k))
                        return real_direct(*a, **k)

                    def stream(*a, **k):
                        given.append(repr(a) + repr(k))
                        return real_stream(*a, **k)
                    c.mp.setattr(api_server, "_query_ollama_direct", direct)
                    c.mp.setattr(api_server, "_stream_ollama_tokens", stream)
                    c.mp.setattr(api_server, "_get_chat_history_dir", lambda: c.tmp)
                    conv = "sec" + re.sub(r"\W", "", path)[:20]
                    body = fn(api_server.app.test_client(), pin, conv)
                    kept = ""
                    if keeps:
                        conv_rec = next((x for x in api_server._chat_history
                                         if x.get("id") == conv), None)
                        kept = json.dumps(conv_rec) if conv_rec else ""
                    echoed = pin in body
                    sent = any(pin in g for g in given)
                    stored = pin in kept
                    rec.update(ok=not (echoed or sent or stored), echoed=echoed,
                               sent_to_model=sent, kept=stored)
                except Exception as exc:  # noqa: BLE001
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                               trace=traceback.format_exc()[-800:])
                out.append(rec)


def section_c(scratch: str, out: list) -> None:
    from pathlib import Path
    from puffpai import api_server
    import test_chat_change_log_and_pins as F
    paths = dict(F._KEYLESS)
    paths.update({
        "keyless /api/chat stream": lambda c, q: c.post("/api/chat", json={
            "stream": True, "messages": [{"role": "user", "content": q}]}),
        "keyless /v1 stream, other model": lambda c, q: c.post("/v1/chat/completions", json={
            "model": "x", "stream": True, "messages": [{"role": "user", "content": q}]}),
        "keyless /conversation": lambda c, q: c.post("/conversation", json={"text": q}),
        "keyless /api/generate": lambda c, q: c.post("/api/generate", json={"prompt": q}),
        "keyless /v1/completions": lambda c, q: c.post("/v1/completions", json={
            "model": "x", "prompt": q}),
        "home assistant without the voice key, stream": lambda c, q: c.post(
            "/v1/chat/completions", headers={"X-Client": "home-assistant"},
            json={"model": "x", "stream": True,
                  "messages": [{"role": "user", "content": q}]}),
    })
    for path, fn in paths.items():
        for q in KEYLESS_QS:
            with Case(scratch) as c:
                rec = {"section": "C keyless gets no owner text", "path": path,
                       "class": "keyless", "ask": q}
                try:
                    seen, _grounding = F._prompt_world(c.mp, Path(c.tmp))
                    resp = fn(api_server.app.test_client(), q)
                    body = resp.get_data(as_text=True)
                    prompts = "\n".join(seen)
                    leaked = [s for s in F._SECRETS if s in prompts or s in body]
                    rec.update(ok=not leaked, leaked=leaked, status=resp.status_code)
                except Exception as exc:  # noqa: BLE001
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                out.append(rec)


class _FakeVault:
    is_initialized = True
    is_legacy = False

    def missing_reason(self):
        return ""

    def screen_pin(self, text):
        return False


def section_d(scratch: str, out: list) -> None:
    from pathlib import Path
    from puffpai import api_server
    import test_chat_false_claims_all_paths as T
    from puffpai.integrate import voice_key
    ask = "what is in my medical records?"
    pin = "Tq7-ravenQ9"

    def keyless_v1(c, q):
        return c.post("/v1/chat/completions", json={
            "model": "x", "messages": [{"role": "user", "content": q}]}).get_data(as_text=True)

    def keyless_v1_stream(c, q):
        return c.post("/v1/chat/completions", json={
            "model": "puffpai", "stream": True,
            "messages": [{"role": "user", "content": q}]}).get_data(as_text=True)

    def keyless_api(c, q):
        return c.post("/api/chat", json={
            "messages": [{"role": "user", "content": q}]}).get_data(as_text=True)

    def keyless_api_stream(c, q):
        return c.post("/api/chat", json={
            "stream": True, "messages": [{"role": "user", "content": q}]}).get_data(as_text=True)

    def ha_no_key(c, q):
        return c.post("/v1/chat/completions", headers={"X-Client": "home-assistant"}, json={
            "model": "x", "messages": [{"role": "user", "content": q}]}).get_data(as_text=True)

    def ha_key(c, q):
        voice_key.forget_cache()
        key = voice_key.new()
        try:
            return c.post("/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key}", "X-Client": "home-assistant"},
                          json={"model": "x", "messages": [{"role": "user", "content": q}]}
                          ).get_data(as_text=True)
        finally:
            voice_key.forget_cache()

    def paired(c, q):
        return c.post("/chat/send", headers=T.PAIRED, json={"message": q}).get_data(as_text=True)

    callers = {"keyless /v1/chat/completions": (keyless_v1, "keyless", False),
               "keyless /v1/chat/completions stream": (keyless_v1_stream, "keyless", False),
               "keyless /api/chat": (keyless_api, "keyless", False),
               "keyless /api/chat stream": (keyless_api_stream, "keyless", False),
               "home assistant without the voice key": (ha_no_key, "keyless", False),
               "home assistant with the voice key": (ha_key, "voice key", False),
               "paired /chat/send (positive control)": (paired, "paired", True)}
    for path, (fn, cls, may) in callers.items():
        with Case(scratch) as c:
            rec = {"section": "D vault from chat", "path": path, "class": cls}
            try:
                T._world(c.mp, Path(c.tmp), "Fine.")
                tried: list = []
                c.mp.setattr(api_server, "_get_vault", lambda: _FakeVault())
                c.mp.setattr(api_server, "_is_protected_query", lambda m: "medical" in str(m))
                c.mp.setattr(api_server, "_vault_may_hold_documents", lambda v: True)
                c.mp.setattr(api_server, "_VAULT_CHAT_STATE", {})

                def unlock(p, q, cid):
                    tried.append(cid)
                    return "Vault unlocked (scripted)."
                c.mp.setattr(api_server, "_try_unlock_and_answer", unlock)
                client = api_server.app.test_client()
                first = fn(client, ask)
                second = fn(client, pin)
                prompted = "reply with your vault PIN" in first
                reached = bool(tried) or "Vault unlocked" in second
                ok = (prompted and reached) if may else not (prompted or reached)
                rec.update(ok=ok, pin_prompt=prompted, unlock_reached=reached,
                           expected="prompt and unlock" if may else "neither")
            except Exception as exc:  # noqa: BLE001
                rec.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                           trace=traceback.format_exc()[-800:])
            out.append(rec)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="folder for the proof files")
    ap.add_argument("--scratch", required=True, help="folder for this run's own state")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.scratch, exist_ok=True)
    os.environ["PUFFPAI_STATE_DIR"] = os.path.join(args.scratch, "state")
    os.environ["PUFFPAI_APPS_ROOT"] = os.path.join(args.scratch, "apps")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    _setup_paths()
    started = time.time()
    results: list = []
    for name, fn in (("A", section_a), ("B", section_b), ("C", section_c), ("D", section_d)):
        t0 = time.time()
        fn(args.scratch, results)
        print(f"section {name}: {time.time() - t0:.1f}s", flush=True)
    failed = [r for r in results if not r.get("ok")]
    head = {"script": "scripts/check_chat_honesty.py", "cases": len(results),
            "failed": len(failed), "seconds": round(time.time() - started, 1)}
    try:
        import subprocess
        head["commit"] = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                                        capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        head["commit"] = ""
    with open(os.path.join(args.out, "fx-honesty_r2_sec_accept.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"run": head, "results": results}, fh, ensure_ascii=False, indent=1)
    lines = [f"# Chat honesty security acceptance ({head['commit']})", "",
             f"{len(results)} cases, {len(failed)} failed, {head['seconds']} s. "
             "Command: py -3.14 scripts/check_chat_honesty.py --out <dir> --scratch <dir>",
             "", "| section | class | passed | failed |", "|---|---|---|---|"]
    tally: dict = {}
    for r in results:
        key = (r["section"], r.get("class", ""))
        p, f = tally.get(key, (0, 0))
        tally[key] = (p + 1, f) if r.get("ok") else (p, f + 1)
    for (sec, cls), (p, f) in sorted(tally.items()):
        lines.append(f"| {sec} | {cls} | {p} | {f} |")
    if failed:
        lines += ["", "## Failed", ""]
        for r in failed[:200]:
            lines.append("- " + json.dumps({k: v for k, v in r.items() if k != "trace"},
                                           ensure_ascii=False)[:400])
    with open(os.path.join(args.out, "fx-honesty_r2_sec_accept.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:4 + len(tally) + 3]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
