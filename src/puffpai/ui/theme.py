"""Frutiger Aero theme constants for PuffPAI.

Extracted from app_ui.py — single source of truth for colours,
typography, layout constants, and avatar style presets.
"""


class PuffPAITheme:
    """Immutable design tokens for the PuffPAI glass-morphism UI."""

    # ── Frutiger Aero Extended Color Palette ──────────────────────────────────
    C_NAVY       = "#005F8A"   # primary navy
    C_BLUE       = "#0096C7"   # sky blue
    C_AQUA       = "#00B4D8"   # aqua
    C_AQUA_MID   = "#48CAE4"   # mid aqua
    C_AQUA_LT    = "#90E0EF"   # light aqua
    C_PALE       = "#CAF0F8"   # pale sky
    C_GLASS      = "#EAF7FC"   # glass tint
    C_BG         = "#D4EFF8"   # main chat background
    C_GREEN      = "#52B788"   # green accent
    C_GREEN_LT   = "#95D5B2"   # light green
    C_WHITE      = "#FFFFFF"
    C_TEXT       = "#0C2F44"   # dark text
    C_TEXT_MED   = "#1E5F80"   # medium text
    C_TEXT_LT    = "#6BAAC9"   # subtle text
    C_BORDER     = "#7EC8E3"   # border / divider
    C_USR_BG     = "#1A9FBF"   # user bubble background
    C_USR_FG     = "#E8FBFF"   # user bubble text (near-white)
    C_AI_BG      = "#F2FCFF"   # AI bubble background (glass)
    C_STATUS_BG  = "#004E78"   # status bar background
    C_GOLD       = "#E9A23B"   # gold accent
    C_RED        = "#D94F4F"   # danger / clear

    # Extra Frutiger Aero accent spectrum
    C_LAVENDER   = "#A78BCC"   # soft purple
    C_LAVENDER_LT= "#D4C5F0"   # pale lavender
    C_ROSE       = "#E07098"   # rose
    C_ROSE_LT    = "#F5C0D4"   # pale rose
    C_MINT       = "#3EAF85"   # mint green
    C_MINT_LT    = "#A8E6CF"   # pale mint
    C_PEACH      = "#E8944A"   # warm peach
    C_PEACH_LT   = "#FAD4A8"   # pale peach
    C_VIOLET     = "#6C63B0"   # deep violet

    # ── Dark Theme Palette ──────────────────────────────────────────────────
    C_DARK_BG         = "#1A1A2E"   # deep background
    C_DARK_CARD       = "#202040"   # card surface
    C_DARK_CARD_HOVER = "#2A2A50"   # card hover
    C_DARK_TEXT       = "#E8E8F0"   # primary text
    C_DARK_TEXT_MED   = "#A0A0C0"   # secondary text
    C_DARK_TEXT_LT    = "#6A6A8A"   # subtle text

    # ── Card / Dashboard Layout Tokens ──────────────────────────────────────
    CARD_CORNER_RADIUS   = 16
    CARD_CORNER_RADIUS_SM = 12
    CARD_PADDING         = 16
    CARD_PADDING_SM      = 10
    TILE_GAP             = 12
    HERO_CORNER_RADIUS   = 20

    # Responsive breakpoints (width in px)
    DASHBOARD_COLS_NARROW = 2     # <= 600px
    DASHBOARD_COLS_MED    = 3     # <= 900px
    DASHBOARD_COLS_WIDE   = 4     # > 900px
    DASHBOARD_BREAKPOINT_SM = 600
    DASHBOARD_BREAKPOINT_MD = 900

    # ── Layout ────────────────────────────────────────────────────────────────
    MAX_CHAT_WIDTH = 780       # centred chat column, px

    # Rotating accent colours for chat-history tiles
    TILE_ACCENTS = [
        "#00B4D8", "#A78BCC", "#3EAF85", "#E07098",
        "#E9A23B", "#0096C7", "#6C63B0", "#52B788",
    ]

    # ── Typography (defaults — overridden after font detection) ───────────────
    _DEFAULT_FAMILY = "Segoe UI"

    FONT      = (_DEFAULT_FAMILY, 10)
    FONT_B    = (_DEFAULT_FAMILY, 10, "bold")
    FONT_SM   = (_DEFAULT_FAMILY, 9)
    FONT_XS   = (_DEFAULT_FAMILY, 8)
    FONT_XS_I = (_DEFAULT_FAMILY, 8, "italic")
    FONT_SM_B = (_DEFAULT_FAMILY, 9, "bold")
    FONT_LG_B = (_DEFAULT_FAMILY, 12, "bold")
    FONT_XL_B = (_DEFAULT_FAMILY, 14, "bold")

    # ── DiceBear Avatar Styles ────────────────────────────────────────────────
    AVATAR_STYLES = [
        "adventurer", "bottts", "personas", "avataaars", "lorelei",
        "notionists", "open-peeps", "pixel-art", "thumbs",
    ]

    # ------------------------------------------------------------------
    # Runtime helpers
    # ------------------------------------------------------------------

    @classmethod
    def detect_font(cls, tk_root=None):
        """Detect best available font family and return updated font tuples.

        Call **once** after a ``tk.Tk()`` window exists.  Returns a dict
        of font-name → tuple that callers can apply to their instance.

        >>> fonts = PuffPAITheme.detect_font()
        >>> fonts['FONT']           # e.g. ('Aptos', 10)
        """
        import tkinter.font as tkfont

        try:
            _f = tkfont.Font(family="Aptos", size=10)
            _ff = _f.actual()["family"].lower()
            family = "Aptos" if "aptos" in _ff else cls._DEFAULT_FAMILY
        except Exception:
            family = cls._DEFAULT_FAMILY

        return {
            "_FF":      family,
            "FONT":     (family, 10),
            "FONT_B":   (family, 10, "bold"),
            "FONT_SM":  (family, 9),
            "FONT_XS":  (family, 8),
            "FONT_XS_I":(family, 8, "italic"),
            "FONT_SM_B":(family, 9, "bold"),
            "FONT_LG_B":(family, 12, "bold"),
            "FONT_XL_B":(family, 14, "bold"),
        }
