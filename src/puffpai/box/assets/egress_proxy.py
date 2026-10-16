#!/usr/bin/python3 -I
"""PuffPAI locked box: allowlisting, logging egress proxy (ported from the proven prototype, proofs 10 and 19).

Runs OUTSIDE bubblewrap as the dedicated `pproxy` user, one instance per run.
Listens on a Unix socket that is the ONLY network path bind-mounted into the
sandbox (the sandbox itself has --unshare-net: loopback only, no DNS).

Rules (fail closed at every step):
  * CONNECT only (HTTPS tunnels). Plain-HTTP proxying is refused (not_encrypted).
  * Target must be a DNS name (no IP literals in any spelling) AND be on the
    run's allowlist as exact host:port. No wildcards.
  * The proxy resolves the name itself; EVERY resolved address must be a public
    unicast address (ipaddress.is_global, plus explicit blocks below). If any
    answer is private/loopback/link-local/CGNAT/multicast/reserved the whole
    request is refused (inside_address), or, when every answer is what a
    home DNS filter gives for a blocked name (0.0.0.0 / ::, 127.0.0.1 / ::1,
    or the DNS server's own address), refused as network_filter, so the
    owner reads "blocked by your network's filter". The proxy then connects to the
    address it vetted, never re-resolving: DNS rebinding has no second lookup
    to win.
  * The first TLS record from the client must be a ClientHello whose SNI
    equals the CONNECT host (stops "CONNECT allowed.com, SNI evil.com" on a
    shared CDN IP). No SNI or not TLS => refused.
  * One JSON line per connection, in the shape of the bot's logs/outside.jsonl
    (puffpai.security.egress_ledger.build_record): t, service, host, path,
    method, outcome(done|refused|failed), code, status, bytes_in, bytes_out,
    what. The Windows backend re-validates every line through build_record
    before it lands in the owner's log.
"""
import argparse
import asyncio
import ipaddress
import json
import os
import re
import sys
from datetime import datetime, timezone

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4", "::/128", "::1/128", "fc00::/7", "fe80::/10",
    "ff00::/8", "64:ff9b::/96", "2002::/16")]
MAX_HEAD = 8192
HEAD_TIMEOUT = 10
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 120


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    return not any(ip in net for net in EXTRA_BLOCKED if net.version == ip.version)


RESOLV_FILES = ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf")
LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal")


def nameservers(files=RESOLV_FILES) -> set:
    """The DNS server addresses this proxy's lookups go to (never raises)."""
    found = set()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "nameserver":
                        try:
                            found.add(str(ipaddress.ip_address(parts[1].split("%", 1)[0])))
                        except ValueError:
                            pass
        except OSError:
            continue
    return found


def filter_block(host: str, addrs, servers=None) -> bool:
    """True when every answer is a home DNS filter's "blocked" answer.

    0.0.0.0 / :: (Pi-hole and AdGuard Home by default), 127.0.0.1 / ::1
    (hosts-file blockers) or the DNS server's own address (Pi-hole "IP" mode).
    Only picks the words: the request is refused either way.
    """
    name = str(host or "").lower().rstrip(".")
    if not addrs or name == "localhost" or name.endswith(LOCAL_SUFFIXES):
        return False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(str(a).split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_unspecified or ip.is_loopback:
            continue
        if is_public(str(ip)):
            return False
        if servers is None:
            servers = nameservers()
        if str(ip) not in servers:
            return False
    return True


def answer_refusal(host: str, addrs, servers=None) -> str:
    """'' when every answer is public, else the refusal code for the log."""
    if addrs and all(is_public(a) for a in addrs):
        return ""
    return "network_filter" if filter_block(host, addrs, servers) else "inside_address"


def looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    # inet_aton spellings: 2130706433, 0x7f.1, 0177.0.0.1, 127.1. No real
    # top-level domain is all digits or starts with 0x, so refuse those.
    last = host.rstrip(".").rsplit(".", 1)[-1]
    return last.isdigit() or last.startswith("0x") or host.replace(".", "").isdigit()


def sni_of(record: bytes):
    """SNI from a TLS ClientHello record, or None. Minimal, bounds-checked."""
    try:
        if len(record) < 5 or record[0] != 0x16:
            return None
        body = record[5:5 + int.from_bytes(record[3:5], "big")]
        if not body or body[0] != 0x01:
            return None
        p = 4 + 2 + 32
        p += 1 + body[p]                                   # session id
        p += 2 + int.from_bytes(body[p:p + 2], "big")      # cipher suites
        p += 1 + body[p]                                   # compression
        end = p + 2 + int.from_bytes(body[p:p + 2], "big")
        p += 2
        while p + 4 <= end:
            etype = int.from_bytes(body[p:p + 2], "big")
            elen = int.from_bytes(body[p + 2:p + 4], "big")
            ext = body[p + 4:p + 4 + elen]
            if etype == 0:
                q = 2
                while q + 3 <= len(ext):
                    ntype, nlen = ext[q], int.from_bytes(ext[q + 1:q + 3], "big")
                    if ntype == 0:
                        return ext[q + 3:q + 3 + nlen].decode("ascii").lower().rstrip(".")
                    q += 3 + nlen
                return None
            p += 4 + elen
    except Exception:
        return None
    return None


class Proxy:
    def __init__(self, allow, log_path, service, what, byte_cap):
        self.allow = allow
        self.log_path = log_path
        self.service = service
        self.what = what
        self.byte_cap = byte_cap
        self.total = 0
        # Opened once at start: if the log cannot be written the proxy does
        # not start, so no traffic can ever pass unlogged (fail closed).
        self.fh = open(log_path, "a", encoding="utf-8")

    def log(self, host, outcome, code="", bi=0, bo=0, method="CONNECT"):
        rec = {"t": now(), "service": self.service, "host": host if HOST_RE.match(host or "") else "",
               "path": "", "method": method, "outcome": outcome, "code": code,
               "status": "", "bytes_in": bi, "bytes_out": bo, "what": self.what}
        self.fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self.fh.flush()

    async def handle(self, reader, writer):
        host, method = "", "CONNECT"
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except Exception:
            writer.close()
            return
        try:
            line = head[:head.index(b"\r\n")].decode("latin-1")
            method, target, _ = line.split(" ", 2)
        except Exception:
            return self.refuse(writer, "", "bad_answer", "GET")
        method = method.upper()
        if method != "CONNECT":
            return self.refuse(writer, "", "not_encrypted", method if method in (
                "GET", "HEAD", "POST", "PUT", "DELETE", "PATCH") else "GET")
        hp = target.rsplit(":", 1)
        if len(hp) != 2 or not hp[1].isdigit():
            return self.refuse(writer, "", "not_allowed")
        host, port = hp[0].strip("[]").lower().rstrip("."), int(hp[1])
        if looks_like_ip(host):
            code = "not_on_list" if is_public(host) else "inside_address"
            return self.refuse(writer, "", code)
        if not HOST_RE.match(host) or (host, port) not in self.allow:
            return self.refuse(writer, host, "not_on_list")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=1)
        except Exception:
            return self.refuse(writer, host, "cannot_look_up", outcome="failed")
        addrs = [i[4][0] for i in infos]
        refused = answer_refusal(host, addrs)
        if refused:
            return self.refuse(writer, host, refused)
        up = None
        for a in addrs:
            try:
                up = await asyncio.wait_for(asyncio.open_connection(a, port), CONNECT_TIMEOUT)
                break
            except Exception:
                continue
        if up is None:
            return self.refuse(writer, host, "no_answer", outcome="failed")
        ur, uw = up
        # From here a real connection is open, so whatever happens next (the
        # client going away, an error, the proxy being stopped) the upstream is
        # closed and exactly one line is written, in the finally below.
        counts = {"out": 0, "in": 0}
        result = ["failed", "no_answer"]
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            try:
                first = await asyncio.wait_for(reader.read(16384), HEAD_TIMEOUT)
            except Exception:
                first = b""
            if sni_of(first) != host:
                result[:] = ["refused", "not_encrypted" if not first or first[0] != 0x16 else "not_allowed"]
                return
            uw.write(first)
            counts["out"] = len(first)
            await self._tunnel(reader, writer, ur, uw, counts)
            code = "too_big" if self.total > self.byte_cap else ""
            result[:] = ["done" if not code else "failed", code]
        except Exception:
            pass                # logged as failed below; a stop (cancel) still propagates
        finally:
            for w in (uw, writer):
                try:
                    w.close()
                except Exception:
                    pass
            self.log(host, result[0], result[1], counts["in"], counts["out"])

    async def _tunnel(self, reader, writer, ur, uw, counts):
        """Copy bytes both ways until either side ends or the byte cap is hit."""

        async def pipe(src, dst, key):
            try:
                while True:
                    data = await asyncio.wait_for(src.read(65536), IDLE_TIMEOUT)
                    if not data:
                        break
                    counts[key] += len(data)
                    self.total += len(data)
                    if self.total > self.byte_cap:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass
        await asyncio.gather(pipe(reader, uw, "out"), pipe(ur, writer, "in"))

    def refuse(self, writer, host, code, method="CONNECT", outcome="refused", sent_ok=False):
        self.log(host, outcome, code, 0, 0, method)
        try:
            if not sent_ok:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            writer.close()
        except Exception:
            pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--allow", default="")
    ap.add_argument("--log", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--what", default="request")
    ap.add_argument("--byte-cap", type=int, default=2 * 1024 ** 3)
    a = ap.parse_args()
    allow = set()
    for item in filter(None, a.allow.split(",")):
        h, _, p = item.partition(":")
        allow.add((h.lower().rstrip("."), int(p or 443)))
    px = Proxy(allow, a.log, a.service, a.what, a.byte_cap)
    os.umask(0o117)
    srv = await asyncio.start_unix_server(px.handle, path=a.socket)
    print("ready", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
