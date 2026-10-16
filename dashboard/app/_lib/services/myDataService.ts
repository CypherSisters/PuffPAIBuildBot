// The owner's data folder and linked folders (src/puffpai/knowledge_folders_api.py).
// All through the paired proxy (/api/pai); the browser holds no key.
//
//   GET    /knowledge/my-data           folders, counts, last scan, waiting
//   POST   /knowledge/data-folder       {folder_path}
//   POST   /knowledge/folders           {folder_path, name?}
//   DELETE /knowledge/folders/<id>
//   POST   /knowledge/my-files/upload   multipart files + relative_path each
//   POST   /knowledge/my-data/scan
//
// Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
import { apiPostRaw } from "@/app/_lib/api";

const BASE = "/api/pai";

export interface DataFolderRow {
  id: string;
  kind: "data" | "previous" | "linked";
  name: string;
  path: string;
  exists: boolean;
  files: number;
  indexed: number;
  waiting: number;
  too_big: number;
  unreadable: number;
  waiting_names: string[];
}

export interface MyDataStatus {
  ok: boolean;
  data_dir: string;
  my_files: string;
  free_space: string;
  auto_index: boolean;
  max_file_mb: number;
  folders: DataFolderRow[];
  last_scan: string;
  indexing: number;
  waiting: number;
  waiting_line: string;
  scan_seconds: number;
  /** The newest files in My files (older backends do not send it). */
  recent_files?: MyFileRow[];
}

export interface MyFileRow {
  name: string;
  /** Its subfolder inside My files, "" at the top. */
  folder: string;
  path: string;
  modified: string;
}

export interface FolderReply {
  ok: boolean;
  message?: string;
  lines?: string[];
  data_dir?: string;
  removed_from_index?: number;
}

export interface UploadRow {
  filename: string;
  status: string;
  message?: string;
}

const NOT_ANSWERING = "Your bot did not answer, so nothing was changed.";

export async function getMyData(): Promise<MyDataStatus | null> {
  try {
    const res = await fetch(`${BASE}/knowledge/my-data`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as MyDataStatus;
  } catch {
    return null;
  }
}

async function post(path: string, body: Record<string, unknown>): Promise<FolderReply> {
  try {
    const reply = await apiPostRaw<FolderReply>(path, body);
    if (reply.status === 403) return { ok: false, message: reply.data?.message || "Pair this device first." };
    return reply.data ?? { ok: false, message: NOT_ANSWERING };
  } catch {
    return { ok: false, message: NOT_ANSWERING };
  }
}

export function setDataFolder(folder: string): Promise<FolderReply> {
  return post("/knowledge/data-folder", { folder_path: folder });
}

export function linkFolder(folder: string): Promise<FolderReply> {
  return post("/knowledge/folders", { folder_path: folder });
}

export async function unlinkFolder(id: string): Promise<FolderReply> {
  try {
    const res = await fetch(`${BASE}/knowledge/folders/${encodeURIComponent(id)}`, { method: "DELETE" });
    return ((await res.json().catch(() => null)) as FolderReply | null) ?? { ok: false, message: NOT_ANSWERING };
  } catch {
    return { ok: false, message: NOT_ANSWERING };
  }
}

export function scanNow(): Promise<FolderReply> {
  return post("/knowledge/my-data/scan", {});
}

/** Many files, or a whole folder (each file's place inside it is kept). */
export async function uploadMyFiles(
  files: File[],
): Promise<{ ok: boolean; results: UploadRow[]; message?: string }> {
  const out: UploadRow[] = [];
  // A few at a time, so one big folder does not become one huge request.
  for (let i = 0; i < files.length; i += 20) {
    const fd = new FormData();
    for (const f of files.slice(i, i + 20)) {
      fd.append("files", f, f.name);
      fd.append("relative_path", (f as File & { webkitRelativePath?: string }).webkitRelativePath || "");
    }
    try {
      const res = await fetch(`${BASE}/knowledge/my-files/upload`, { method: "POST", body: fd });
      const data = (await res.json().catch(() => null)) as { results?: UploadRow[]; message?: string } | null;
      if (!res.ok) return { ok: false, results: out, message: data?.message || NOT_ANSWERING };
      out.push(...(data?.results ?? []));
    } catch {
      return { ok: false, results: out, message: NOT_ANSWERING };
    }
  }
  return { ok: true, results: out };
}

/** One plain line for what an upload batch did. */
export function uploadSummary(rows: UploadRow[]): string {
  const n = (s: string) => rows.filter((r) => r.status === s).length;
  const parts: string[] = [];
  const added = n("indexed") + n("in_vault");
  if (added) parts.push(`${added} added`);
  if (n("waiting")) parts.push(`${n("waiting")} waiting for the vault`);
  if (n("too_big")) parts.push(`${n("too_big")} too big`);
  if (n("unreadable") + n("empty")) parts.push(`${n("unreadable") + n("empty")} could not be read`);
  return parts.length ? parts.join(", ") + "." : "Nothing was added.";
}
