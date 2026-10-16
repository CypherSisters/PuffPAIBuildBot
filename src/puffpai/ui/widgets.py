"""Frutiger Aero drawing helpers for PuffPAI.

Extracted from app_ui.py — canvas-based rounded rectangles, glass
bubbles, gradient headers, bokeh backgrounds, and hover effects.
"""

import tkinter as tk
from PIL import Image, ImageDraw

from .theme import PuffPAITheme


class AeroWidgets:
    """Stateless drawing helpers that operate on tkinter Canvases.

    Instantiate once per window with a resolved theme (post font-detection).
    """

    def __init__(self, theme: PuffPAITheme, root_window: tk.Tk,
                 font_family: str = "Segoe UI"):
        self.theme = theme
        self.root = root_window
        self._FF = font_family

    # ── Rounded rectangles ────────────────────────────────────────────────

    @staticmethod
    def rounded_rect(canvas, x, y, w, h, r=18, **kw):
        """Draw a smooth rounded rectangle on *canvas* using polygon+smooth."""
        pts = [
            x+r, y,    x+w-r, y,
            x+w, y,    x+w,   y+r,
            x+w, y+h-r, x+w,  y+h,
            x+w-r, y+h, x+r,  y+h,
            x,   y+h,   x,    y+h-r,
            x,   y+r,   x,    y,
        ]
        return canvas.create_polygon(pts, smooth=True, **kw)

    # ── Colour helpers ────────────────────────────────────────────────────

    @staticmethod
    def tint(hex_color: str, amount: float) -> str:
        """Blend *hex_color* toward white by *amount* (0-1)."""
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── Chat bubble ──────────────────────────────────────────────────────

    def make_rounded_bubble(self, parent, text, bg_color, fg_color,
                            font, wraplength=480, radius=20,
                            padx=14, pady=10):
        """Return a Frame containing a Canvas-drawn rounded bubble."""
        outer = tk.Frame(parent, bg=parent["bg"])

        # Measure required dimensions via a transient Label
        _tmp = tk.Label(self.root, text=text, font=font,
                        wraplength=wraplength, justify=tk.LEFT,
                        padx=padx, pady=pady)
        self.root.update_idletasks()
        bw = max(_tmp.winfo_reqwidth(), 60)
        bh = _tmp.winfo_reqheight()
        _tmp.destroy()

        c = tk.Canvas(outer, width=bw, height=bh,
                      bg=parent["bg"], highlightthickness=0, bd=0)
        c.pack()

        # Filled rounded background
        self.rounded_rect(c, 0, 0, bw, bh, r=radius,
                          fill=bg_color, outline="")

        # Glass gloss highlight at top-left (Frutiger Aero signature)
        gloss_h = max(bh // 3, 12)
        gloss_pts = [
            radius, 0,  bw - radius, 0,
            bw - radius, gloss_h // 2,
            radius, gloss_h,
        ]
        c.create_polygon(gloss_pts, smooth=True,
                         fill=self.tint(bg_color, 0.22), outline="")

        # Text rendered directly on the canvas
        c.create_text(padx, pady, text=text, fill=fg_color,
                      font=font, width=wraplength,
                      anchor="nw", justify="left")

        return outer

    # ── Bokeh background ─────────────────────────────────────────────────

    def draw_bg_bubbles(self, canvas, w, h):
        """Draw decorative Frutiger Aero bokeh bubbles on *canvas*."""
        canvas.delete("bg_bubble")
        if w < 10 or h < 10:
            return

        T = self.theme
        bubble_specs = [
            (0.10, 0.08,  80, T.C_AQUA_LT),
            (0.85, 0.10,  60, T.C_LAVENDER_LT),
            (0.92, 0.60, 100, T.C_MINT_LT),
            (0.06, 0.65,  70, T.C_ROSE_LT),
            (0.55, 0.88,  90, T.C_PALE),
            (0.68, 0.40,  55, T.C_PEACH_LT),
            (0.30, 0.40,  45, T.C_GREEN_LT),
            (0.45, 0.15,  38, T.C_LAVENDER_LT),
            (0.75, 0.78,  65, T.C_AQUA_LT),
        ]
        for xp, yp, r, color in bubble_specs:
            cx, cy = int(xp * w), int(yp * h)
            # Outer glow ring
            canvas.create_oval(cx-r-5, cy-r-5, cx+r+5, cy+r+5,
                               fill="", outline=color, width=2,
                               tags="bg_bubble")
            # Main fill
            canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                               fill=color, outline="",
                               tags="bg_bubble")
            # Glass specular highlight
            hr = max(r // 4, 4)
            hx, hy = cx - r // 3, cy - r // 2
            canvas.create_oval(hx-hr, hy-hr, hx+hr, hy+hr,
                               fill="#FFFFFF", outline="",
                               tags="bg_bubble")
        canvas.tag_lower("bg_bubble")

    # ── Hover binding ────────────────────────────────────────────────────

    @staticmethod
    def bind_hover(widget, normal_color, hover_color):
        """Attach mouse-over colour transitions to *widget*."""
        widget.bind("<Enter>", lambda *_: widget.config(bg=hover_color))
        widget.bind("<Leave>", lambda *_: widget.config(bg=normal_color))

    # ── Sidebar header gradient ──────────────────────────────────────────

    def draw_header(self, canvas, logo_photo=None, width=None, height=None):
        """Render Frutiger Aero gradient on the sidebar header canvas."""
        w = width or canvas.winfo_width()
        h = height or canvas.winfo_height()
        if w <= 1:
            w = 280
        if h <= 1:
            h = 130

        canvas.delete("all")
        T = self.theme

        # Vertical gradient: navy → aqua
        r1, g1, b1 = 0, 95, 138
        r2, g2, b2 = 0, 180, 216
        for i in range(h):
            t = i / max(h - 1, 1)
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            canvas.create_line(0, i, w, i, fill=f"#{r:02x}{g:02x}{b:02x}")

        # Glass gloss strip
        for i in range(30):
            alpha = 0.30 * (1.0 - i / 30)
            r = int(255 * alpha + r1 * (1 - alpha))
            g = int(255 * alpha + g1 * (1 - alpha))
            b = int(255 * alpha + b1 * (1 - alpha))
            canvas.create_line(0, i, w, i, fill=f"#{r:02x}{g:02x}{b:02x}")

        # Chrome bottom edge
        canvas.create_line(0, h - 2, w, h - 2, fill=T.C_PALE, width=1)
        canvas.create_line(0, h - 1, w, h - 1, fill=T.C_NAVY, width=1)

        if logo_photo:
            canvas.create_image(w // 2, h // 2 - 4,
                                image=logo_photo, anchor="center")
        else:
            canvas.create_text(w // 2, h // 2 - 10,
                               text="PuffPAI",
                               fill=T.C_WHITE,
                               font=(self._FF, 18, "bold"))
            canvas.create_text(w // 2, h // 2 + 14,
                               text="Privacy-Forward AI",
                               fill=T.C_PALE,
                               font=(self._FF, 8))

    # ── Circle crop (avatar) ─────────────────────────────────────────────

    @staticmethod
    def circle_crop(img, size):
        """Crop a PIL Image to a circle with transparent background."""
        img = img.resize((size, size), Image.LANCZOS)
        mask = Image.new('L', (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        result.paste(img, mask=mask)
        return result
