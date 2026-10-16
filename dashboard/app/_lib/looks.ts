/**
 * Looks: putting the owner's chosen look on the page, as data only.
 *
 * The backend sends, for each mode (day / night), a map of CSS custom
 * property names to values it wrote itself (src/puffpai/looks/package.py
 * css_vars). The browser is stricter-or-equal: it re-checks every NAME
 * against LOOK_VAR_KINDS and every VALUE against the pattern for that
 * name's kind, and only ever calls style.setProperty on <html>. Never a
 * <style> string, never innerHTML, never a URL.
 *
 * The same tables drive the inline boot script (LOOK_BOOT_SCRIPT, placed in
 * app/layout.tsx right after the day/night script), so the first paint already
 * wears the look and there is one copy of the rules. tests/test_looks_*.py
 * checks these tables equal the backend's.
 *
 * Escape hatch: ?puffLook=default shows the bot's own look for this browser
 * session (in case a look is ever hard to read); ?puffLook=on ends that.
 *
 * Pure module (no "use client"): layout.tsx (a server component) imports the
 * boot script string from here; the functions touch the DOM only when called.
 */

export type LookMode = "day" | "night";
export type LookVars = Record<string, string>;
export type LookBothModes = { day: LookVars; night: LookVars };

/** Allowed property name -> the kind of value it may hold. */
export const LOOK_VAR_KINDS: Readonly<Record<string, string>> = {
  "--bg": "colour",
  "--bg2": "colour",
  "--glow1": "colour",
  "--glow2": "colour",
  "--card": "colour",
  "--card2": "colour",
  "--line": "colour",
  "--text": "colour",
  "--text2": "colour",
  "--text3": "colour",
  "--pink": "colour",
  "--magenta": "colour",
  "--sky": "colour",
  "--mint": "colour",
  "--lemon": "colour",
  "--lav": "colour",
  "--railBg": "colour",
  "--chip": "colour",
  "--puff-text-strong": "colour",
  "--puff-card-hover": "colour",
  "--puff-surface-deep": "colour",
  "--puff-red": "colour",
  "--puff-glow": "colour",
  "--puff-on-accent": "colour",
  "--shadow": "shadow",
  "--puff-radius-card": "px",
  "--puff-radius-button": "px",
  "--puff-font-heading": "font",
  "--puff-font-body": "font",
  "--img1": "amount",
  "--img2": "amount",
  "--img3": "amount",
  "--img4": "amount",
  "--imgTile": "amount",
  "--imgPage": "amount",
  "--puff-scene-glow1": "colour",
  "--puff-scene-glow2": "colour",
  "--puff-scene-end": "colour",
  "--puff-vignette": "colour",
  "--puff-star": "colour",
  "--puff-stars": "amount",
  "--puff-stars-more": "amount",
  "--puff-petals": "amount",
  "--puff-confetti": "amount",
  "--puff-hero-glow": "amount",
};

/** Value kind -> the only shape it may have (anchored). */
export const LOOK_VALUE_PATTERNS: Readonly<Record<string, string>> = {
  colour: "^(?:#[0-9a-f]{6}|rgba\\(\\d{1,3},\\d{1,3},\\d{1,3},(?:0|1|0?\\.\\d{1,3})\\))$",
  shadow: "^0 18px \\d{1,2}px rgba\\(\\d{1,3},\\d{1,3},\\d{1,3},(?:0|1|0?\\.\\d{1,3})\\)$",
  px: "^\\d{1,4}px$",
  font: "^var\\(--font-(?:fredoka|nunito|jbmono)\\)$",
  amount: "^(?:0|1|0?\\.\\d{1,3})$",
};

export const LOOK_CACHE_KEY = "puff-look-cache";
export const LOOK_OFF_KEY = "puff-look-off";

const RES: Record<string, RegExp> = Object.fromEntries(
  Object.entries(LOOK_VALUE_PATTERNS).map(([k, v]) => [k, new RegExp(v)]),
);

/** True when *name* may be set and *value* has the right shape for it. */
export function lookValueOk(name: string, value: unknown): value is string {
  const kind = Object.prototype.hasOwnProperty.call(LOOK_VAR_KINDS, name)
    ? LOOK_VAR_KINDS[name]
    : undefined;
  return kind !== undefined && typeof value === "string" && RES[kind].test(value);
}

/** Only the allowed, well-shaped entries of *vars*. */
export function cleanVars(vars: unknown): LookVars {
  const out: LookVars = {};
  if (!vars || typeof vars !== "object") return out;
  for (const [name, value] of Object.entries(vars as Record<string, unknown>)) {
    if (lookValueOk(name, value)) out[name] = value;
  }
  return out;
}

export function cleanBoth(both: unknown): LookBothModes | null {
  if (!both || typeof both !== "object") return null;
  const b = both as Record<string, unknown>;
  const day = cleanVars(b.day);
  const night = cleanVars(b.night);
  if (!Object.keys(day).length && !Object.keys(night).length) return null;
  return { day, night };
}

type StyleLike = {
  setProperty(name: string, value: string): void;
  removeProperty(name: string): string;
  getPropertyValue(name: string): string;
};

/**
 * Set exactly *vars* (after cleaning) on *style*; every other allowed name is
 * removed so the page's own values show through. Touches a property only
 * when it is wrong, so an observer that calls this cannot loop.
 */
export function setVars(style: StyleLike, vars: LookVars | null): void {
  const clean = cleanVars(vars);
  for (const name of Object.keys(LOOK_VAR_KINDS)) {
    const want = clean[name];
    const have = style.getPropertyValue(name);
    if (want !== undefined) {
      if (have !== want) style.setProperty(name, want);
    } else if (have) {
      style.removeProperty(name);
    }
  }
}

export function currentMode(): LookMode {
  return document.documentElement.getAttribute("data-puff-theme") === "day" ? "day" : "night";
}

type LookWindow = Window & { __puffLook?: LookBothModes | null; __puffLookApply?: () => void };

/** Is the ?puffLook=default escape hatch on for this browser session? */
export function lookSwitchedOff(): boolean {
  try {
    return sessionStorage.getItem(LOOK_OFF_KEY) === "1";
  } catch {
    return false;
  }
}

/** Fired on window whenever a look is worn or taken off on this page. */
export const LOOK_CHANGED = "puffpai:look-changed";

/** Is a picked look being worn on this page (not the bot's own colours)? */
export function lookWorn(): boolean {
  try {
    return Boolean((window as LookWindow).__puffLook) && !lookSwitchedOff();
  } catch {
    return false;
  }
}

/**
 * Wear *both* on this page now (null = the bot's own look). Nothing is saved:
 * this is "Try it on". Day/night flips keep following it.
 */
export function wearLook(both: LookBothModes | null): void {
  const w = window as LookWindow;
  w.__puffLook = both ? cleanBoth(both) : null;
  const apply = () => setVars(document.documentElement.style, (w.__puffLook || null)?.[currentMode()] || null);
  if (!w.__puffLookApply) {
    w.__puffLookApply = apply;
    new MutationObserver(() => w.__puffLookApply && w.__puffLookApply()).observe(
      document.documentElement,
      { attributes: true, attributeFilter: ["data-puff-theme", "style"] },
    );
  }
  apply();
  window.dispatchEvent(new Event(LOOK_CHANGED));
}

/** Remember *both* for the next page load in this browser (null = own look). */
export function saveLook(both: LookBothModes | null): void {
  try {
    const clean = both ? cleanBoth(both) : null;
    if (clean) localStorage.setItem(LOOK_CACHE_KEY, JSON.stringify(clean));
    else localStorage.removeItem(LOOK_CACHE_KEY);
  } catch {
    /* storage disabled: the look still applies to this page */
  }
}

/** The saved look, as the boot script would read it. */
export function savedLook(): LookBothModes | null {
  try {
    return cleanBoth(JSON.parse(localStorage.getItem(LOOK_CACHE_KEY) || "null"));
  } catch {
    return null;
  }
}

/**
 * Runs before first paint (inline in app/layout.tsx, after the day/night
 * script). Built from the same tables as above.
 */
export const LOOK_BOOT_SCRIPT =
  "(function(){try{" +
  "var K=" + JSON.stringify(LOOK_VAR_KINDS) + ";" +
  "var P=" + JSON.stringify(LOOK_VALUE_PATTERNS) + ";" +
  "var q=new URLSearchParams(location.search).get('puffLook');" +
  "if(q==='default'){sessionStorage.setItem('" + LOOK_OFF_KEY + "','1');}" +
  "else if(q==='on'){sessionStorage.removeItem('" + LOOK_OFF_KEY + "');}" +
  "if(sessionStorage.getItem('" + LOOK_OFF_KEY + "')==='1'){return;}" +
  "var c=JSON.parse(localStorage.getItem('" + LOOK_CACHE_KEY + "')||'null');" +
  "if(!c||typeof c!=='object'){return;}" +
  "window.__puffLook=c;" +
  "var r=document.documentElement;" +
  "var ap=function(){var m=r.getAttribute('data-puff-theme')==='day'?'day':'night';" +
  "var v=(window.__puffLook||{})[m]||{};" +
  "for(var n in K){var x=v[n];" +
  "if(typeof x==='string'&&new RegExp(P[K[n]]).test(x)){if(r.style.getPropertyValue(n)!==x)r.style.setProperty(n,x);}" +
  "else if(r.style.getPropertyValue(n)){r.style.removeProperty(n);}}};" +
  "window.__puffLookApply=ap;ap();" +
  "new MutationObserver(function(){window.__puffLookApply&&window.__puffLookApply();})" +
  ".observe(r,{attributes:true,attributeFilter:['data-puff-theme','style']});" +
  "}catch(e){}})();";
