/**
 * Toolsmith's model: on this computer (the default) or Anthropic, the owner's
 * choice. Every call goes through the same-origin /api/pai proxy (prefix
 * "toolsmith" in proxyAllowlist.ts); the backend needs a paired device.
 *
 * THE KEY NEVER STAYS IN THE BROWSER. It is sent once in a POST body and the
 * backend keeps it in the vault. Nothing here stores it or logs it.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

const BASE = "/api/pai";

export interface ToolsmithModelRow {
  id: string;
  name: string;
}

export interface ToolsmithModel {
  where: "local" | "cloud";
  model: string;
  models: ToolsmithModelRow[];
  consent_text: string;
  key_state: string;
  spent_line: string;
  /** Set while Toolsmith holds its key for the session (the vault may be locked). */
  lease_line: string;
  /** How long its key stays ready while she builds: "1h" (default), "4h" or "lock". */
  keep: string;
  keep_question: string;
  keep_choices: ToolsmithModelRow[];
  /** Set while she picked Anthropic in the wizard but its key is not saved. */
  hold_line: string;
  /** Her yes was to an earlier consent sentence: Toolsmith is on this computer until she agrees again. */
  consent_again: boolean;
  line: string;
}

export interface Reply {
  ok: boolean;
  data: ToolsmithModel | null;
  message: string;
  /** The HTTP status (0: no answer). 423 = the vault is locked. */
  status: number;
  /** The server's refusal code, e.g. "vault_locked" or "before_setup". */
  code: string;
}

function read(raw: unknown): ToolsmithModel | null {
  const d = (raw && typeof raw === "object" ? raw : null) as Record<string, unknown> | null;
  if (!d || (d.where !== "local" && d.where !== "cloud")) return null;
  const models = Array.isArray(d.models)
    ? (d.models as unknown[])
        .map((m) => (m && typeof m === "object" ? (m as Record<string, unknown>) : {}))
        .filter((m) => typeof m.id === "string" && typeof m.name === "string")
        .map((m) => ({ id: m.id as string, name: m.name as string }))
    : [];
  const s = (v: unknown) => (typeof v === "string" ? v : "");
  const keepChoices = Array.isArray(d.keep_choices)
    ? (d.keep_choices as unknown[])
        .map((m) => (m && typeof m === "object" ? (m as Record<string, unknown>) : {}))
        .filter((m) => typeof m.id === "string" && typeof m.name === "string")
        .map((m) => ({ id: m.id as string, name: m.name as string }))
    : [];
  return {
    where: d.where,
    model: s(d.model),
    models,
    consent_text: s(d.consent_text),
    key_state: s(d.key_state),
    spent_line: s(d.spent_line),
    lease_line: s(d.lease_line),
    keep: s(d.keep),
    keep_question: s(d.keep_question),
    keep_choices: keepChoices,
    hold_line: s(d.hold_line),
    consent_again: d.consent_again === true,
    line: s(d.line),
  };
}

async function call(path: string, method = "GET", body?: unknown): Promise<Reply> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    let data: Record<string, unknown> = {};
    try {
      data = (await res.json()) as Record<string, unknown>;
    } catch {
      /* empty body */
    }
    const message = typeof data.message === "string" ? data.message : "";
    const code = typeof data.code === "string" ? data.code : "";
    return { ok: res.ok, data: read(data), message, status: res.status, code };
  } catch {
    return { ok: false, data: null, message: "BuildBot could not be reached.", status: 0, code: "" };
  }
}

export const getToolsmithModel = () => call("/toolsmith/model");

export const chooseLocal = () => call("/toolsmith/model", "POST", { where: "local" });

/** Her yes to Anthropic (the consent text was on screen). `key` only when pasted now. */
export const chooseAnthropic = (model: string, key?: string) =>
  call("/toolsmith/model", "POST", key ? { where: "cloud", model, key } : { where: "cloud", model });

/** How long Toolsmith's key stays ready while she builds: "1h", "4h" or "lock". */
export const chooseKeep = (keep: string) => call("/toolsmith/model/keep", "POST", { keep });

export const forgetKey = () => call("/toolsmith/model/forget-key", "POST", {});
