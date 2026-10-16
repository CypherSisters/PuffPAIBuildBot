// "Add your own" on the Developer page: her programs, her desktop apps and
// opening her files. All through the paired proxy (/api/pai).
//
//   GET    /programs                 {programs: [Program], installed_desktop: [{name, path}]}
//   POST   /programs                 {kind: "cli" | "desktop", path? | command?, name?}
//   PATCH  /programs/<id>            {always_allow}
//   DELETE /programs/<id>
//   POST   /programs/<id>/launch     {file?}
//   POST   /files/open               {path} (her data folder, My files or a linked folder only)
//
// A 404 means this build does not have these routes yet: the cards say
// "Coming soon" instead of an error.

export interface ProgramCommand {
  name?: string;
  what?: string;
}

export interface Program {
  id: string;
  name: string;
  kind: "cli" | "desktop";
  path: string;
  command: string;
  added_at: string;
  help: string;
  commands: (string | ProgramCommand)[];
  always_allow: boolean;
}

export interface DesktopApp {
  name: string;
  path: string;
}

export interface Reply<T> {
  ok: boolean;
  /** The route is not in this build yet. */
  missing: boolean;
  data: T;
  message: string;
}

const NO_ANSWER = "Your bot did not answer, so nothing was changed.";

async function call<T>(path: string, method: string, body?: unknown): Promise<Reply<T | null>> {
  try {
    const res = await fetch(`/api/pai${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      /* empty body */
    }
    const said = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
    const message =
      typeof said.message === "string" ? said.message : typeof said.error === "string" ? said.error : typeof said.line === "string" ? said.line : "";
    return { ok: res.ok && said.ok !== false, missing: res.status === 404, data: data as T, message };
  } catch {
    return { ok: false, missing: false, data: null, message: NO_ANSWER };
  }
}

const ID = /^[A-Za-z0-9_-]{1,100}$/;

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function toProgram(raw: unknown): Program | null {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  if (!ID.test(str(r.id))) return null;
  return {
    id: str(r.id),
    name: str(r.name) || str(r.command) || str(r.path),
    kind: r.kind === "desktop" ? "desktop" : "cli",
    path: str(r.path),
    command: str(r.command),
    added_at: str(r.added_at),
    help: str(r.help),
    commands: Array.isArray(r.commands) ? (r.commands as (string | ProgramCommand)[]).slice(0, 40) : [],
    always_allow: r.always_allow === true,
  };
}

export async function listPrograms(): Promise<Reply<{ programs: Program[]; installed_desktop: DesktopApp[] }>> {
  const r = await call<Record<string, unknown>>("/programs", "GET");
  const d = r.data || {};
  const programs = Array.isArray(d.programs) ? d.programs.map(toProgram).filter((p): p is Program => p !== null) : [];
  const installed_desktop = Array.isArray(d.installed_desktop)
    ? (d.installed_desktop as Record<string, unknown>[])
        .map((a) => ({ name: str(a?.name), path: str(a?.path) }))
        .filter((a) => a.name && a.path)
    : [];
  return { ...r, data: { programs, installed_desktop } };
}

export async function addProgram(body: {
  kind: "cli" | "desktop";
  path?: string;
  command?: string;
  name?: string;
}): Promise<Reply<Program | null>> {
  const r = await call<Record<string, unknown>>("/programs", "POST", body);
  const d = r.data || {};
  return { ...r, data: toProgram(d.program ?? d) };
}

export async function setAlwaysAllow(id: string, always_allow: boolean): Promise<Reply<unknown>> {
  if (!ID.test(id)) return { ok: false, missing: false, data: null, message: "" };
  return call(`/programs/${id}`, "PATCH", { always_allow });
}

export async function removeProgram(id: string): Promise<Reply<unknown>> {
  if (!ID.test(id)) return { ok: false, missing: false, data: null, message: "" };
  return call(`/programs/${id}`, "DELETE");
}

export async function launchProgram(id: string, file?: string): Promise<Reply<unknown>> {
  if (!ID.test(id)) return { ok: false, missing: false, data: null, message: "" };
  return call(`/programs/${id}/launch`, "POST", file ? { file } : {});
}

/** Open one of her files in the program her computer uses for it. */
export async function openFile(path: string): Promise<Reply<unknown>> {
  return call("/files/open", "POST", { path: path.slice(0, 1000) });
}

export function commandWords(c: string | ProgramCommand): string {
  if (typeof c === "string") return c;
  return [c.name, c.what].filter(Boolean).join(": ");
}
