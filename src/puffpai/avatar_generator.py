"""
PuffPAI Avatar Generator
========================
Generates avatars using PIL/Pillow (primary) or DiceBear (optional).

Primary: PIL gradient circle with initials — no external dependencies.
Optional: Node.js + DiceBear + resvg for styled avatars.

Generated PNGs are cached in assets/avatars/ so generation only happens
once per unique seed/style combination.

Node is started only by full path from :mod:`puffpai.security.host_programs`,
from its fixed working folder with its small environment.
No trusted node means the PIL avatar, never a node found by name.
"""

import os
import io
import base64
import hashlib
import logging
import subprocess

from puffpai import procflags

from PIL import Image, ImageDraw, ImageFont

from puffpai.security import host_programs

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_AVATAR_DIR = os.path.join(_HERE, 'avatar')
# Generated avatars are cached under the data dir (honours PUFFPAI_DATA_DIR) so
# the source tree / installed package stays clean and read-only-install safe.
_DATA_DIR = os.environ.get(
    "PUFFPAI_DATA_DIR", os.path.join(_HERE, '..', '..'))
_CACHE_DIR = os.path.join(_DATA_DIR, 'assets', 'avatars')

# Palette for PIL generation — matches Frutiger Aero accent colours
_FALLBACK_COLORS = [
    (0, 180, 216),    # aqua
    (167, 139, 204),  # lavender
    (62, 175, 133),   # mint
    (224, 112, 152),  # rose
    (233, 162, 59),   # gold
    (108, 99, 176),   # violet
    (82, 183, 136),   # green
    (0, 150, 199),    # blue
]


def get_initials(name: str) -> str:
    """Extract initials from a name (up to 2 characters).

    "John Smith" → "JS", "PuffPAI" → "P", "" → "?"
    """
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def generate_avatar_pil(name: str, size: int = 200) -> Image.Image:
    """Generate a PIL avatar: gradient circle with initials.

    This is the PRIMARY avatar generation method — no Node.js required.

    Args:
        name: The name to derive colour and initials from.
        size: Image size in pixels (square).

    Returns:
        RGBA PIL Image.
    """
    h = hashlib.md5(name.encode()).digest()
    color = _FALLBACK_COLORS[h[0] % len(_FALLBACK_COLORS)]

    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Filled circle with slight gradient effect (lighter inner circle)
    margin = max(1, size // 16)
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color + (255,),
    )

    # Lighter inner highlight for depth
    inner_margin = size // 4
    lighter = tuple(min(255, c + 40) for c in color) + (80,)
    draw.ellipse(
        [inner_margin, inner_margin, size - inner_margin - size // 8, size - inner_margin - size // 8],
        fill=lighter,
    )

    # Initials centred
    initials = get_initials(name)
    font_size = size // 3 if len(initials) > 1 else size // 2
    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), initials, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), initials, fill=(255, 255, 255, 230), font=font)

    return img


def _load_font(size: int):
    """Try common system fonts, fall back to PIL default."""
    candidates = [
        'aptos.ttf', 'seguivar.ttf', 'segoeui.ttf',
        'arial.ttf', 'DejaVuSans.ttf',
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class AvatarGenerator:
    """Avatar generator with PIL primary + optional DiceBear enhancement.

    Primary method: PIL gradient circle with initials (always available).
    Optional enhancement: DiceBear via Node.js (only if explicitly requested
    and Node.js is installed).
    """

    def __init__(self):
        os.makedirs(_CACHE_DIR, exist_ok=True)
        self._node_ok: bool | None = None  # Lazy-check on first DiceBear request

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(self, seed: str, style: str = 'adventurer', size: int = 64,
                 use_dicebear: bool = False) -> Image.Image:
        """Return a PIL Image for the given seed and style.

        Args:
            seed: Name/identifier used for colour and initials.
            style: DiceBear style (only used if use_dicebear=True).
            size: Image size in pixels.
            use_dicebear: If True AND Node.js available, use DiceBear.
                          Default is False — PIL is primary.

        Returns:
            RGBA PIL Image.
        """
        cache_key = hashlib.md5(
            f"{seed}:{style if use_dicebear else 'pil'}:{size}".encode()
        ).hexdigest()
        cache_path = os.path.join(_CACHE_DIR, f"{cache_key}.png")

        if os.path.exists(cache_path):
            return Image.open(cache_path).convert('RGBA')

        if use_dicebear and self._check_node():
            img = self._from_dicebear(seed, style, size)
        else:
            img = generate_avatar_pil(seed, size)

        img.save(cache_path)
        return img

    def clear_cache(self):
        """Remove all cached avatar PNGs."""
        for fname in os.listdir(_CACHE_DIR):
            if fname.endswith('.png'):
                os.remove(os.path.join(_CACHE_DIR, fname))

    # ── DiceBear via Node.js (optional) ────────────────────────────────────────

    def _from_dicebear(self, seed: str, style: str, size: int) -> Image.Image:
        """Call generate_avatar.js which outputs base64-encoded PNG via resvg."""
        try:
            node = host_programs.path('node')
            script = os.path.join(_AVATAR_DIR, 'generate_avatar.js')
            if not node or not os.path.isfile(script):
                return generate_avatar_pil(seed, size)
            # The script finds its own parts next to itself, so it runs from
            # host_programs' fixed working folder like every other program.
            result = subprocess.run(
                [node, script, style, seed, 'b6e3f4', str(size)],
                capture_output=True, text=True, timeout=15,
                **host_programs.run_kwargs(),
                **procflags.hidden(),
            )
            if result.returncode != 0:
                logger.warning("DiceBear Node error: %s", result.stderr.strip())
                return generate_avatar_pil(seed, size)

            png_bytes = base64.b64decode(result.stdout)
            return Image.open(io.BytesIO(png_bytes)).convert('RGBA')

        except Exception as exc:
            logger.warning("DiceBear generation failed: %s", exc)
            return generate_avatar_pil(seed, size)

    # ── Checks ─────────────────────────────────────────────────────────────────

    def _check_node(self) -> bool:
        """Lazy-check if the trusted Node.js (full path only) is available."""
        if self._node_ok is None:
            try:
                node = host_programs.path('node')
                if not node:
                    self._node_ok = False
                    return False
                r = subprocess.run(
                    [node, '--version'],
                    capture_output=True, text=True, timeout=5,
                    **host_programs.run_kwargs(),
                    **procflags.hidden(),
                )
                self._node_ok = r.returncode == 0
            except Exception:
                self._node_ok = False
        return self._node_ok
