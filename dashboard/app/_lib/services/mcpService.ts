// Tool servers (MCP): "Connect my Home Assistant", "Add my own tool", and
// "learn a tool from its docs". Through the same-origin proxy (/api/pai), which
// carries the backend key; the browser holds none. A token or password is sent
// once in a POST body, goes straight into the vault, and is never sent back.
//
//   GET  /mcp/servers                  the saved tool servers (never a password)
//   POST /mcp/servers                  {name, target, password?} add one
//   POST /mcp/servers/<name>/remove    remove one
//   POST /mcp/home-assistant           {address, token, keep_working_locked}
//   POST /mcp/learn                    {url?, text?, name?} draft a skill
//   POST /mcp/learn/<name>/keep        {text?} her yes
//   POST /mcp/learn/<name>/discard     throw it away

const BASE = "/api/pai";

export interface Reply<T = any> {
  ok: boolean;
  status: number;
  data: T;
}

export interface ToolServer {
  name: string;
  label: string;
  kind: "http" | "sse" | "stdio";
  address: string;
  has_password: boolean;
  tools: string[];
  tool_count: number;
  switched_on: boolean;
  added_at: string;
  source: string;
}

export interface ServerList {
  servers: ToolServer[];
  ready: boolean;
  home_assistant_address: string;
}

export interface Said {
  ok: boolean;
  message: string;
  code?: string;
}

export interface Draft extends Said {
  name?: string;
  description?: string;
  text?: string;
}

async function req<T = any>(path: string, method = "GET", body?: unknown): Promise<Reply<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    let data: any = {};
    try {
      data = await res.json();
    } catch {
      /* empty body */
    }
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: { ok: false, message: "Your bot did not answer." } as T };
  }
}

export const listToolServers = () => req<ServerList>("/mcp/servers");

export const connectHomeAssistant = (address: string, token: string, keepWorkingLocked: boolean) =>
  req<Said>("/mcp/home-assistant", "POST", { address, token, keep_working_locked: keepWorkingLocked });

export const addToolServer = (name: string, target: string, password: string) =>
  req<Said>("/mcp/servers", "POST", { name, target, password });

export const removeToolServer = (name: string) =>
  req<Said>(`/mcp/servers/${encodeURIComponent(name)}/remove`, "POST");

export const learnFromDocs = (url: string, text: string, name: string) =>
  req<Draft>("/mcp/learn", "POST", { url, text, name });

export const keepLearnedSkill = (name: string, text: string) =>
  req<Said>(`/mcp/learn/${encodeURIComponent(name)}/keep`, "POST", { text });

export const discardLearnedSkill = (name: string) =>
  req<Said>(`/mcp/learn/${encodeURIComponent(name)}/discard`, "POST");
