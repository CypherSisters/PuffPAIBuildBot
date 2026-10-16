"""System tray integration for PuffPAI.

Provides minimize-to-tray functionality and a tray icon with context menu.
Uses ``pystray`` for cross-platform system tray support.

Usage:
    from puffpai.ui.system_tray import SystemTray

    tray = SystemTray(on_show=my_show_fn, on_quit=my_quit_fn)
    tray.start()              # starts tray icon in background thread
    tray.notify("Hello!")     # show a tray notification
    tray.stop()               # cleanup

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_PYSTRAY = True
except ImportError:
    pystray = None  # type: ignore[assignment]
    _HAS_PYSTRAY = False


def _create_default_icon(size: int = 64) -> "Image.Image":
    """Generate a simple PuffPAI tray icon (aqua circle with P)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Aqua circle
    draw.ellipse([2, 2, size - 2, size - 2], fill="#00B4D8")

    # "P" letter
    try:
        font = ImageFont.truetype("segoeui.ttf", size=size // 2)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arial.ttf", size=size // 2)
        except (OSError, IOError):
            font = ImageFont.load_default()  # type: ignore[assignment]

    bbox = draw.textbbox((0, 0), "P", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), "P", fill="white", font=font)

    return img


class SystemTray:
    """Cross-platform system tray icon with context menu."""

    def __init__(
        self,
        on_show: Callable | None = None,
        on_quit: Callable | None = None,
        on_new_chat: Callable | None = None,
        icon_image: "Image.Image | None" = None,
    ):
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_new_chat = on_new_chat
        self._icon_image = icon_image
        self._tray: "pystray.Icon | None" = None
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def available(self) -> bool:
        """True if pystray is installed and usable."""
        return _HAS_PYSTRAY

    def start(self) -> bool:
        """Start the system tray icon in a background thread.

        Returns True if started successfully, False if pystray is unavailable.
        """
        if not _HAS_PYSTRAY:
            logger.info("pystray not installed — system tray disabled")
            return False

        if self._running:
            return True

        icon_img = self._icon_image or _create_default_icon()

        menu = pystray.Menu(
            pystray.MenuItem("Show PuffPAI", self._handle_show, default=True),
            pystray.MenuItem("New Chat", self._handle_new_chat),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._handle_quit),
        )

        self._tray = pystray.Icon(
            name="PuffPAI",
            icon=icon_img,
            title="PuffPAI: Privacy-Forward AI",
            menu=menu,
        )

        self._thread = threading.Thread(
            target=self._tray.run, daemon=True, name="puffpai-tray")
        self._thread.start()
        self._running = True
        logger.info("System tray icon started")
        return True

    def stop(self) -> None:
        """Stop the tray icon and clean up."""
        if self._tray and self._running:
            try:
                self._tray.stop()
            except Exception as e:
                logger.debug("Tray stop error: %s", e)
            self._running = False
            logger.info("System tray icon stopped")

    def notify(self, message: str, title: str = "PuffPAI") -> None:
        """Show a system notification from the tray icon."""
        if self._tray and self._running:
            try:
                self._tray.notify(message, title)
            except Exception as e:
                logger.debug("Tray notification error: %s", e)

    # ── Menu handlers ─────────────────────────────────────────────────

    def _handle_show(self, icon=None, item=None):
        if self._on_show:
            self._on_show()

    def _handle_new_chat(self, icon=None, item=None):
        if self._on_new_chat:
            self._on_new_chat()

    def _handle_quit(self, icon=None, item=None):
        if self._on_quit:
            self._on_quit()
