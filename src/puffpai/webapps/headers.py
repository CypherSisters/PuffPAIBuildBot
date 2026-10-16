"""The headers on every answer from the app host.

The Content-Security-Policy is the wall around a browser app (the injected
shim is only a convenience). It is the measured prototype policy
(``appbridge/appserver.py`` ``CSP_APP``) with two additions:

* ``frame-ancestors`` names exactly the owner's dashboard (``localhost`` and
  ``127.0.0.1`` on the dashboard port), so no other page can frame the app;
* ``webrtc 'block'`` is sent as a request only. By itself it does NOT stop
  WebRTC: STUN and TURN packets were measured leaving an app page and its
  ``srcdoc`` child in Edge 154 and Chrome 153 with it in place. What stops
  it (measured in both: no packet) is the guard
  (``runner/guard.js``), which takes ``RTCPeerConnection`` off the page
  before the app's code runs, with every frame the app makes sealed with no
  script, so no clean copy can be fetched from a child. That is a script
  wall in the page, not a browser setting: a browser feature that offers
  another way to make a peer connection would not be covered. The static
  scan still holds apps whose code makes direct connections unless their
  purpose is calls, and the bot's own hidden browser keeps its proxy-only
  UDP flags.

Resource hints are not covered by any CSP directive browsers honour: a
``<link rel=prerender>`` sent a request carrying page data past this
policy, from the page's markup, from its script and from any frame it made
(measured in Edge 154 and Chrome 153; COEP, COOP,
``prefetch-src``, a CSP sandbox and ``credentialless`` did not stop it).
So, fail closed:

* the host makes hints inert in every HTML page it serves, gives every frame
  in the markup ``sandbox=""`` first, never sends ``Link`` or
  ``Speculation-Rules`` headers, sends ``X-DNS-Prefetch-Control: off``, never
  serves a service worker (one could answer the app's own pages with no
  CSP at all: measured, a plain ``fetch`` then reached any host) and never
  serves SVG or XML as a page (:mod:`puffpai.webapps.host`);
* ``frame-src 'self'``: no ``blob:`` or ``data:`` frames, which run script
  the guard is not in (and the guard and the host make no frame at all);
* ``require-trusted-types-for 'script'; trusted-types default``: the guard
  (``runner/guard.js``, first in every page) owns the only policy, so every
  HTML string the app writes is cleaned and a ``javascript:`` address never
  opens; the guard also keeps script from giving a link a hint and gives
  every frame the app makes ``sandbox=""``.

Moves. Moving the page itself, or a frame it made, to
another address was blocked by the dashboard's and the app's ``frame-src``,
but the browser opened a connection there first (no request, but the
address's name is looked up, so a name could carry a little data out;
measured, Edge 154). Now the guard cancels every move of the page to an
address that is not the app's own through the browser's own "navigate"
event, before the browser starts it, and no frame is ever made (the
app's script could move one through ``frames[i].location``, which nothing
in a page can intercept). Measured again: no connection on any route
(``scripts/check_app_frame_leaks.py``). Still not covered: a move the browser
makes without a cancelable "navigate" event (back or forward in history;
the app's frame has no history outside its own address), and a browser
without the Navigation API (none BuildBot supports).

``'unsafe-inline'`` and ``'unsafe-eval'`` stay: real apps are one inline script
or an ``eval`` bundle, and each app is alone on its own address, so they only
put the app at risk from itself.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations


def dashboard_origins(dash_port: int) -> list[str]:
    port = int(dash_port)
    return [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]


def csp(dash_port: int) -> str:
    return ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            "media-src 'self' data: blob:; connect-src 'self'; "
            "worker-src 'self' blob:; frame-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            "manifest-src 'self'; "
            "frame-ancestors " + " ".join(dashboard_origins(dash_port)) + "; "
            "require-trusted-types-for 'script'; trusted-types default; "
            "webrtc 'block'; "
            "report-uri /__puffpai/report")


PERMISSIONS_POLICY = ("camera=(), microphone=(), geolocation=(), usb=(), "
                      "serial=(), hid=(), bluetooth=(), payment=(), "
                      "display-capture=(), clipboard-read=()")


def app_headers(dash_port: int) -> dict[str, str]:
    """Every answer carries these. ``Access-Control-Allow-Origin`` never does."""
    return {
        "Content-Security-Policy": csp(dash_port),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-DNS-Prefetch-Control": "off",
        "Permissions-Policy": PERMISSIONS_POLICY,
    }


__all__ = ["csp", "app_headers", "dashboard_origins", "PERMISSIONS_POLICY"]
