"use client";

/**
 * The page an app gets when nobody wrote it one.
 *
 * Every app this build SHIPS has a hand-written page next to this file, and
 * Next resolves static segments before dynamic ones, so `/dashboard/apps/
 * calendar` still lands on `calendar/page.tsx`. This route is for the app an
 * owner adds: `apps/<id>/app.json` declares `ui.route` pointing at
 * `/dashboard/apps/<id>`, the nav derives its entry from that route — and
 * until this file existed, following that entry was a 404. A declarative app
 * could be installed, could hold data, and could not be looked at.
 *
 * WHAT IT RENDERS, AND WHY THAT AND NOT MORE
 *
 * The backend copies a manifest out by name and bounds every field before it
 * reaches a browser (`app_registry.ui_records`): id, name, blurb, route, icon,
 * surfaces, and whether the build can currently meet what the app says it
 * needs. So this page shows those, plus the app's own data store, read from
 * the same generic `/apps/<id>/data` endpoints the shipped pages use.
 *
 * A manifest's `vocabulary` and `actions` are NOT served to the browser, so
 * this page does not list them and does not pretend to. It says where those
 * words do work — in conversation, because installing an app registers its
 * keywords with the router — rather than drawing buttons that dispatch
 * nowhere. `/apps/<id>/action` only answers an allowlist of methods that exist
 * in Python; a manifest cannot add to it, and a button that looked like it
 * could would be the exact lie this page was added to stop telling.
 *
 * The write path is real and it is the generic one: pull the document, change
 * it, push it back with PUT. That is what an app gets without anybody writing
 * code.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  mdiAlertCircleOutline,
  mdiCheck,
  mdiDelete,
  mdiPencil,
  mdiPlus,
  mdiRefresh,
} from "@mdi/js";
import Button from "@/app/_components/Button";
import Icon from "@/app/_components/Icon";
import {
  AppPage,
  FREDOKA,
  JBM,
  NUNITO,
  Panel,
  SectionBar,
  microLabel,
} from "../_components/ui";
import { apiGet, apiPut } from "@/app/_lib/api";
import {
  getBuildSelf,
  installedApps,
  notifyBuildChanged,
} from "@/app/_lib/services/buildService";
import {
  deletePersona,
  listPersonas,
  notInThisBuild,
  patchPersona,
  personaProblem,
  type OwnerApp,
  type PersonaPatch,
} from "@/app/_lib/services/personaService";
import { appIconPath } from "@/app/_lib/surfaceMap";
import { stashChatQuery } from "@/app/_lib/chatHandoff";
import FeedCard from "../_components/FeedCard";
import { feedThingId, useJobRedirect } from "@/app/_lib/hooks/useJobRedirect";
import FeedDraftCard from "../_components/FeedDraftCard";
import WebAppFrame, { WEB_APP_ID } from "./WebAppFrame";
import WebAppHeader from "./AppHeader";
import BotApps from "./BotApps";

/** What `/build/self` says about one app. A superset of the shared
 *  `InstalledApp` type: the two degradation fields are served but not carried
 *  there, and this page is the one that has room to explain them. */
type AppRecord = {
  id: string;
  name: string;
  blurb: string;
  route: string;
  icon: string;
  surfaces: string[];
  degraded?: boolean;
  degraded_reason?: string;
  requires?: string[];
};

type AppDocument = Record<string, unknown>;

/** Keys the store keeps for itself. Shown nowhere; they are bookkeeping. */
const HIDDEN_KEYS = new Set(["last_updated"]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** One value, as short text. Objects and arrays are JSON so nothing renders
 *  "[object Object]" at somebody who is trying to read their own data. */
function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

/** The columns a list of records needs: every key any row declares, in the
 *  order they first appear. Schema-driven, because the schema is the app
 *  author's and we have never seen it. */
function columnsOf(rows: unknown[]): string[] {
  const out: string[] = [];
  for (const row of rows) {
    if (!isPlainObject(row)) continue;
    for (const key of Object.keys(row)) {
      if (!out.includes(key)) out.push(key);
    }
  }
  return out;
}

/**
 * A browser app Toolsmith added (its id has the issued shape
 * `<slug>-<8 base32>`, which no declarative app id can have: those use no
 * hyphen) opens in its locked frame; every other app gets the page below.
 */
export default function GenericAppPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id ?? "");
  if (WEB_APP_ID.test(id)) return <WebAppPage id={id} />;
  return <DeclarativeAppPage />;
}

/** The friendly name the build knows for a browser app, else its id's words. */
function WebAppPage({ id }: { id: string }) {
  const [name, setName] = useState(() => id.replace(/-[a-z2-7]{8}$/, "").replace(/-/g, " "));
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const build = await getBuildSelf();
        const apps = installedApps(build) as AppRecord[] | null;
        const found = apps?.find((entry) => entry.id === id);
        if (!cancelled && found && typeof found.name === "string" && found.name.trim()) {
          setName(found.name.trim().slice(0, 60));
        }
      } catch {
        /* the id's own words stay */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);
  return (
    <div style={{ padding: "12px 16px" }}>
      <WebAppHeader id={id} name={name} />
      <WebAppFrame id={id} title={name} />
    </div>
  );
}

function DeclarativeAppPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = String(params?.id ?? "");
  // A live card a job uses lives on that job's page now (one build foundation).
  useJobRedirect(feedThingId(id));

  const [app, setApp] = useState<AppRecord | null>(null);
  /** This app's record from GET /persona/list, when it is one the OWNER made.
   *  Null means "not one of theirs, or not known yet" — either way no Delete is
   *  offered, because deleting something we are not sure is theirs is the one
   *  mistake here that cannot be undone. */
  const [persona, setPersona] = useState<OwnerApp | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [known, setKnown] = useState<boolean | null>(null);
  const [doc, setDoc] = useState<AppDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [addTo, setAddTo] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);

  // ── editing an app the owner made ─────────────────────────────────────────
  /** False once a PATCH has come back 404, which means this backend does not
   *  have the editor at all. Starts true: the dashboard cannot know without
   *  asking, and a probe would be a write. */
  const [editSupported, setEditSupported] = useState(true);
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [listDraft, setListDraft] = useState("");
  const [newLists, setNewLists] = useState<string[]>([]);
  /** {stored key -> new label}, only for the ones actually retyped. */
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editSaved, setEditSaved] = useState(false);

  const loadDoc = useCallback(async () => {
    if (!id) return;
    try {
      setDoc(await apiGet<AppDocument>(`/apps/${id}/data`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "the store did not answer");
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      // The build answers what this app IS; the store answers what it holds.
      // They are independent: an app can be installed with nothing in it, and
      // a backend that cannot describe the build can still serve the store.
      const build = await getBuildSelf();
      if (cancelled) return;
      const apps = installedApps(build) as AppRecord[] | null;
      if (apps) {
        setKnown(true);
        setApp(apps.find((entry) => entry.id === id) ?? null);
      } else {
        // Not known is not "not installed". Fail open, the way the nav does.
        setKnown(false);
        setApp(null);
      }
      await loadDoc();
      // Independent of both: whether this is an app the owner made decides only
      // whether Delete is offered. A backend that will not answer this question
      // leaves it unoffered, which is the safe direction.
      try {
        const reply = await listPersonas();
        if (!cancelled) {
          setPersona(reply.apps.find((entry) => entry.id === id) ?? null);
        }
      } catch {
        if (!cancelled) setPersona(null);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [id, loadDoc]);

  /** The document's top-level entries, split into the ones worth a table and
   *  the ones that are a single value. */
  const sections = useMemo(() => {
    const lists: { key: string; rows: unknown[] }[] = [];
    const fields: { key: string; value: unknown }[] = [];
    for (const [key, value] of Object.entries(doc ?? {})) {
      if (HIDDEN_KEYS.has(key)) continue;
      if (Array.isArray(value)) lists.push({ key, rows: value });
      else fields.push({ key, value });
    }
    return { lists, fields };
  }, [doc]);

  /** Pull, change, push — the generic write path, done on the document we are
   *  already holding rather than a fresh read, so two tabs cannot silently
   *  merge into each other without either of them being told. */
  const writeList = useCallback(
    async (key: string, rows: unknown[]) => {
      if (!doc) return;
      setSaving(true);
      try {
        const next = { ...doc, [key]: rows };
        await apiPut(`/apps/${id}/data`, next);
        setDoc(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "the store refused the write");
        await loadDoc();
      } finally {
        setSaving(false);
      }
    },
    [doc, id, loadDoc],
  );

  const addItem = useCallback(
    async (key: string) => {
      const text = draft.trim();
      if (!text) {
        setDraftError("Type the item first.");
        return;
      }
      // Structured text (an object with named parts) keeps its parts; anything
      // else is saved as the words typed.
      let parsed: unknown = text;
      if (text.startsWith("{")) {
        try {
          parsed = JSON.parse(text);
        } catch {
          setDraftError("That could not be read. Type the item as plain words.");
          return;
        }
        if (!isPlainObject(parsed)) {
          setDraftError("That could not be read. Type the item as plain words.");
          return;
        }
      }
      setDraftError(null);
      const rows = Array.isArray(doc?.[key]) ? (doc[key] as unknown[]) : [];
      await writeList(key, [...rows, parsed]);
      setDraft("");
      setAddTo(null);
    },
    [doc, draft, writeList],
  );

  /** The sentences this app understands, generated from ITS OWN lists.
   *
   *  Read off the store's array keys rather than from the manifest's
   *  `vocabulary`, which the backend deliberately does not serve to a browser.
   *  That is not a shortcut: the chat fastpath derives its grammar from the same
   *  `data.default` list keys, so a hint built from the keys is a hint about
   *  something that really works. A phrase copied out of `vocabulary` would not
   *  be — keywords are what the ROUTER matches, not what the parser answers. */
  const sayHints = useMemo(
    () =>
      sections.lists.map(({ key }) => {
        const label = key.replace(/_/g, " ").trim();
        return {
          key,
          label,
          add: `add <item> to the ${label} list`,
          show: `what is on the ${label} list`,
        };
      }),
    [sections.lists],
  );

  const askInChat = useCallback(
    (sentence: string) => {
      if (stashChatQuery(sentence)) router.push("/dashboard/chat");
    },
    [router],
  );

  /** Delete this persona, its pack and its store. Offered only for an app the
   *  owner made, and only once they have typed its id back. */
  const removeThis = useCallback(async () => {
    if (!persona || typed.trim() !== id || deleting) return;
    setDeleting(true);
    try {
      await deletePersona(id, typed.trim());
      // The rail and the apps index are mounted above this page and would keep
      // linking to something that is gone.
      notifyBuildChanged();
      router.push("/dashboard/apps");
    } catch (err) {
      setError(personaProblem(err));
      setDeleting(false);
    }
  }, [deleting, id, persona, router, typed]);

  /** Re-read what this app IS after an edit: the build record (name, route) and
   *  the owner record (lists, the Try sentence). Same two calls the mount effect
   *  makes, so an edited app is described the way a freshly loaded one is. */
  const reloadIdentity = useCallback(async () => {
    const build = await getBuildSelf();
    const apps = installedApps(build) as AppRecord[] | null;
    if (apps) {
      setKnown(true);
      setApp(apps.find((entry) => entry.id === id) ?? null);
    }
    try {
      const reply = await listPersonas();
      setPersona(reply.apps.find((entry) => entry.id === id) ?? null);
    } catch {
      /* the edit already landed; a failed re-read is not a failed edit */
    }
  }, [id]);

  const addNewList = useCallback(() => {
    const value = listDraft.trim();
    if (!value) return;
    const taken = [
      ...newLists,
      ...(persona?.list_labels ?? []),
      ...(persona?.lists ?? []),
    ].map((entry) => entry.toLowerCase());
    if (!taken.includes(value.toLowerCase())) setNewLists((cur) => [...cur, value]);
    setListDraft("");
  }, [listDraft, newLists, persona]);

  /** Send only what changed. An empty patch is not sent at all: the backend
   *  would accept it and the page would claim a save that saved nothing. */
  const saveEdit = useCallback(async () => {
    if (!persona || savingEdit) return;
    const patch: PersonaPatch = {};
    const wantName = nameDraft.trim();
    if (wantName && wantName !== persona.name) patch.name = wantName;
    if (newLists.length > 0) patch.add_lists = newLists;
    // Keyed by the STORED key, not the label: the key is what the manifest, the
    // store and the chat grammar all use, so it is the one spelling that cannot
    // be ambiguous. The value is the new label, as typed.
    const renamePairs = Object.entries(renames).filter(([key, value]) => {
      const next = value.trim();
      return next.length > 0 && next !== key;
    });
    if (renamePairs.length > 0) {
      patch.rename_lists = Object.fromEntries(
        renamePairs.map(([key, value]) => [key, value.trim()]),
      );
    }
    if (Object.keys(patch).length === 0) {
      setEditError("Nothing to save yet. Change the name or add a list.");
      return;
    }
    setSavingEdit(true);
    setEditError(null);
    try {
      await patchPersona(id, patch);
      setNewLists([]);
      setRenames({});
      setEditSaved(true);
      // The rail and the apps index are mounted above this page and are holding
      // the old name.
      notifyBuildChanged();
      await reloadIdentity();
      await loadDoc();
    } catch (err) {
      if (notInThisBuild(err)) {
        setEditSupported(false);
        setEditing(false);
      } else {
        setEditError(personaProblem(err));
      }
    } finally {
      setSavingEdit(false);
    }
  }, [id, loadDoc, nameDraft, newLists, persona, reloadIdentity, renames, savingEdit]);

  if (loading) {
    return (
      <AppPage>
        <div className="flex items-center justify-center py-20">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[var(--sky)] border-t-transparent" />
        </div>
      </AppPage>
    );
  }

  // Known list, and this id is not on it. Say so plainly and say how it is
  // fixed, rather than showing an empty page that reads like a broken one.
  if (known === true && !app) {
    return (
      <AppPage>
        {/* A live-data app BuildBot made and the owner has not said yes to.
            Renders nothing when there is no such draft. */}
        <FeedDraftCard
          draftId={id}
          onInstalled={() => {
            reloadIdentity();
            loadDoc();
          }}
        />
        <Panel accent="--lemon">
          <div className="flex items-start gap-3">
            <Icon path={mdiAlertCircleOutline} size="20" className="mt-0.5 text-[var(--lemon)]" />
            <div>
              <p className="font-semibold text-[var(--text)]">
                There is no app called “{id}” on this bot.
              </p>
              <p className="mt-1 text-sm text-[var(--text2)]">
                It may have been removed. To add something new, open Find more.
              </p>
            </div>
          </div>
        </Panel>
      </AppPage>
    );
  }

  const title = app?.name || id;

  return (
    <AppPage>
      {/* ---- Header: the app's own words, rendered as text ---- */}
      <Panel>
        <div className="flex items-start gap-4">
          <div
            className="flex h-12 w-12 flex-shrink-0 items-center justify-center"
            style={{ borderRadius: 16, background: "var(--chip)", color: "var(--sky)" }}
          >
            <Icon path={appIconPath(app?.icon ?? "")} size="24" />
          </div>
          <div className="min-w-0 flex-1">
            <h1
              style={{
                margin: 0,
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: "clamp(22px,1.9vw,30px)",
                letterSpacing: "-.5px",
                lineHeight: 1.1,
                color: "var(--text)",
              }}
            >
              {title}
            </h1>
            {app?.blurb && (
              <p
                style={{
                  margin: "5px 0 0",
                  fontFamily: NUNITO,
                  fontWeight: 500,
                  fontSize: 15,
                  lineHeight: 1.4,
                  color: "var(--text2)",
                }}
              >
                {app.blurb}
              </p>
            )}
          </div>
          <Button icon={mdiRefresh} label="Reload" color="lightDark" small roundedFull onClick={loadDoc} />
        </div>
        {app?.degraded && app.degraded_reason && (
          <p
            className="mt-3.5 pl-3"
            style={{
              borderLeft: "3px solid var(--lemon)",
              fontFamily: NUNITO,
              fontWeight: 600,
              fontSize: 14,
              color: "var(--lemon)",
            }}
          >
            {app.degraded_reason}
          </p>
        )}
      </Panel>

      {error && (
        <Panel accent="--puff-red">
          <div className="flex items-center gap-2">
            <Icon path={mdiAlertCircleOutline} size="20" />
            <span>{error}</span>
          </div>
        </Panel>
      )}

      {/* ---- The bot's own page: its apps, one row per job ---- */}
      {persona?.own && <BotApps name={persona.name} />}

      {/* ---- Live data, when the app has a feed (renders nothing otherwise) ---- */}
      <FeedCard appId={id} />

      {/* ---- The store ---- */}
      {(sections.fields.length > 0 || sections.lists.length > 0) && (
        <SectionBar title="What it keeps" />
      )}

      {sections.fields.length > 0 && (
        <Panel style={{ padding: 0, overflow: "hidden" }}>
          <table className="w-full text-left text-sm">
            <tbody className="divide-y divide-[var(--line)]">
              {sections.fields.map((field) => (
                <tr key={field.key}>
                  <td className="w-1/3 px-4 py-2 font-mono text-xs text-[var(--text2)]">
                    {field.key}
                  </td>
                  <td className="px-4 py-2 text-[var(--text)]">
                    {cellText(field.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}


      {sections.lists.map(({ key, rows }) => {
        const columns = columnsOf(rows);
        return (
          <div key={key} className="mb-6">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-mono text-sm font-semibold text-[var(--text)]">
                {key}
                <span className="ml-2 font-sans text-xs font-normal text-[var(--text3)]">
                  {rows.length} {rows.length === 1 ? "item" : "items"}
                </span>
              </h2>
              <Button
                icon={mdiPlus}
                label="Add item"
                color="lightDark"
                small
                roundedFull
                disabled={saving}
                onClick={() => {
                  setAddTo(addTo === key ? null : key);
                  setDraft("");
                  setDraftError(null);
                }}
              />
            </div>

            {addTo === key && (
              <Panel className="mb-2">
                <p className="mb-2 text-xs text-[var(--text2)]">
                  Type one item. It is added to the end of the list and saved.
                </p>
                <textarea
                  className="w-full rounded-lg border border-[var(--line)] bg-[var(--card2)] p-2 font-mono text-xs text-[var(--text)]"
                  rows={3}
                  spellCheck={false}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="e.g. bench 2"
                />
                {draftError && (
                  <p className="mt-1 text-xs text-[var(--puff-red)]">{draftError}</p>
                )}
                <div className="mt-2 flex gap-2">
                  <Button
                    label="Save"
                    color="info"
                    small
                    roundedFull
                    disabled={saving}
                    onClick={() => addItem(key)}
                  />
                  <Button
                    label="Cancel"
                    color="lightDark"
                    small
                    roundedFull
                    onClick={() => setAddTo(null)}
                  />
                </div>
              </Panel>
            )}

            {rows.length === 0 ? (
              <Panel className="text-center text-[var(--text3)]">
                <p className="py-4 text-sm">Nothing in {key} yet.</p>
              </Panel>
            ) : (
              <Panel style={{ padding: 0, overflow: "hidden" }}>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-[var(--line)]">
                        {(columns.length > 0 ? columns : ["value"]).map((column) => (
                          <th
                            key={column}
                            className="px-4 py-2 font-mono text-xs font-semibold text-[var(--text2)]"
                          >
                            {column}
                          </th>
                        ))}
                        <th className="w-10 px-4 py-2" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--line)]">
                      {rows.map((row, index) => (
                        <tr
                          key={index}
                          className="transition hover:bg-[var(--puff-card-hover)]"
                        >
                          {columns.length > 0 ? (
                            columns.map((column) => (
                              <td
                                key={column}
                                className="px-4 py-2 text-[var(--text)]"
                              >
                                {isPlainObject(row) ? cellText(row[column]) : ""}
                              </td>
                            ))
                          ) : (
                            <td className="px-4 py-2 text-[var(--text)]">
                              {cellText(row)}
                            </td>
                          )}
                          <td className="px-4 py-2 text-right">
                            <button
                              type="button"
                              aria-label={`Remove item ${index + 1} from ${key}`}
                              disabled={saving}
                              className="text-[var(--text3)] transition hover:text-[var(--puff-red)] disabled:opacity-40"
                              onClick={() =>
                                writeList(
                                  key,
                                  rows.filter((_, position) => position !== index),
                                )
                              }
                            >
                              <Icon path={mdiDelete} size="16" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            )}
          </div>
        );
      })}

      {/* ---- What the manifest's words actually do ---- */}
      {/* Was SectionTitleLineWithButton with no children, which meant the old
          component drew a cog BUTTON here — a control wired to nothing at all.
          The heading was wanted; the button never was. */}
      {sayHints.length > 0 && <SectionBar title="Things you can say" />}
      {sayHints.length > 0 && (
        <Panel>
          <p style={{ ...microLabel, marginBottom: 10 }}>Say</p>
          <div className="flex flex-col gap-2">
            {sayHints.map((hint) => (
              <div
                key={hint.key}
                className="flex flex-wrap items-center gap-2 text-sm"
                style={{ fontFamily: NUNITO, color: "var(--text)" }}
              >
                <code style={{ fontFamily: JBM, fontSize: 12 }}>{hint.add}</code>
                <span style={{ color: "var(--text3)" }}>·</span>
                <code style={{ fontFamily: JBM, fontSize: 12 }}>{hint.show}</code>
                <span style={{ color: "var(--text3)" }}>·</span>
                <code style={{ fontFamily: JBM, fontSize: 12 }}>
                  remove &lt;item&gt; from the {hint.label} list
                </code>
              </div>
            ))}
          </div>
          {persona?.try && (
            <div className="mt-3.5">
              <Button
                label={`Try: ${persona.try}`}
                color="info"
                small
                roundedFull
                onClick={() => askInChat(persona.try)}
              />
            </div>
          )}
          <p className="mt-3 text-sm text-[var(--text2)]">
            Say these in chat or out loud. Adding, showing, removing and counting
            work even without the AI model, and nothing is called saved until it
            really is.
          </p>
        </Panel>
      )}

      {/* ---- Edit, for an app the owner made and only for one ---- */}
      {/* Three edits and no more: rename the app, add a list, relabel a list.
          There is deliberately no "remove a list" — its contents would go with
          it, and the store is the only copy. Deleting the whole app is below,
          behind a typed confirmation, which is the honest place for that. */}
      {persona && (
        <>
          <SectionBar title="Edit it" />
          {!editSupported ? (
            <Panel>
              <p className="text-sm text-[var(--text2)]">
                Changing this app from here is not in this version of your
                bot yet.
              </p>
            </Panel>
          ) : !editing ? (
            <Panel>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-[var(--text2)]">
                  Rename {persona.name}, add a list, or relabel one it already
                  keeps. Nothing is ever dropped by an edit.
                </p>
                <Button
                  icon={mdiPencil}
                  label="Edit this app"
                  color="lightDark"
                  small
                  roundedFull
                  onClick={() => {
                    setEditing(true);
                    setNameDraft(persona.name);
                    setNewLists([]);
                    setRenames({});
                    setEditError(null);
                    setEditSaved(false);
                  }}
                />
              </div>
              {editSaved && (
                <p className="mt-2 flex items-center gap-1.5 text-sm text-[var(--mint)]">
                  <Icon path={mdiCheck} size="16" /> Saved.
                </p>
              )}
            </Panel>
          ) : (
            <Panel>
              {/* Name */}
              <label
                className="text-sm"
                htmlFor="persona-name"
                style={{ fontFamily: NUNITO, color: "var(--text2)" }}
              >
                Name
              </label>
              <input
                id="persona-name"
                value={nameDraft}
                onChange={(event) => setNameDraft(event.target.value)}
                autoComplete="off"
                className="mt-1 w-full max-w-sm rounded-xl border border-[var(--line)] bg-[var(--card2)] px-3 py-2 text-sm text-[var(--text)]"
              />
              <p style={{ ...microLabel, marginTop: 6 }}>
                Its short name stays {id}, so nothing it keeps moves
              </p>

              {/* Rename the lists it keeps */}
              {(persona.lists ?? []).length > 0 && (
                <div className="mt-4">
                  <p style={{ ...microLabel, marginBottom: 8 }}>Its lists</p>
                  {persona.lists.map((key, index) => (
                    <div key={key} className="mb-2 flex flex-wrap items-center gap-2">
                      <code
                        style={{ fontFamily: JBM, fontSize: 12, color: "var(--text3)" }}
                      >
                        {key}
                      </code>
                      <input
                        aria-label={`New name for the ${key} list`}
                        value={renames[key] ?? persona.list_labels?.[index] ?? key}
                        onChange={(event) =>
                          setRenames((current) => ({
                            ...current,
                            [key]: event.target.value,
                          }))
                        }
                        autoComplete="off"
                        className="w-full max-w-xs rounded-xl border border-[var(--line)] bg-[var(--card2)] px-3 py-1.5 text-sm text-[var(--text)]"
                      />
                    </div>
                  ))}
                  <p style={{ ...microLabel }}>
                    A renamed list keeps everything in it
                  </p>
                </div>
              )}

              {/* Add lists */}
              <div className="mt-4">
                <p style={{ ...microLabel, marginBottom: 8 }}>Add a list</p>
                {newLists.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {newLists.map((entry) => (
                      <button
                        key={entry}
                        type="button"
                        title={`Do not add the ${entry} list`}
                        onClick={() =>
                          setNewLists((current) =>
                            current.filter((item) => item !== entry),
                          )
                        }
                        className="rounded-full border border-[var(--line)] px-3 py-1"
                        style={{
                          background: "var(--chip)",
                          fontFamily: JBM,
                          fontSize: 12,
                          fontWeight: 700,
                          color: "var(--text2)",
                        }}
                      >
                        {entry} ✕
                      </button>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    aria-label="A list to add"
                    value={listDraft}
                    onChange={(event) => setListDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addNewList();
                      }
                    }}
                    placeholder="e.g. harvest log"
                    autoComplete="off"
                    className="w-full max-w-xs rounded-xl border border-[var(--line)] bg-[var(--card2)] px-3 py-1.5 text-sm text-[var(--text)]"
                  />
                  <Button
                    icon={mdiPlus}
                    label="Add"
                    color="lightDark"
                    small
                    roundedFull
                    disabled={!listDraft.trim()}
                    onClick={addNewList}
                  />
                </div>
              </div>

              {editError && (
                <p className="mt-3 text-sm text-[var(--puff-red)]">{editError}</p>
              )}

              <div className="mt-4 flex gap-2">
                <Button
                  label={savingEdit ? "Saving…" : "Save changes"}
                  color="info"
                  small
                  roundedFull
                  disabled={savingEdit}
                  onClick={saveEdit}
                />
                <Button
                  label="Cancel"
                  color="lightDark"
                  small
                  roundedFull
                  disabled={savingEdit}
                  onClick={() => {
                    setEditing(false);
                    setNewLists([]);
                    setRenames({});
                    setEditError(null);
                  }}
                />
              </div>
            </Panel>
          )}
        </>
      )}

      {/* ---- Delete, for an app the owner made and only for one ---- */}
      {/* A shipped app has no Delete here at all, and the backend refuses one
          even if this component is wrong about which is which: DELETE /persona/
          <id> touches nothing outside the owner's own apps directory. */}
      {/* Not on the bot's own page: she did not add it as an app, and
          removing it from here would remove her bot's place (item 20). */}
      {persona && !persona.own && (
        <>
          <SectionBar title="Remove it" />
          <Panel accent="--puff-red">
            <p
              className="text-sm"
              style={{ fontFamily: NUNITO, color: "var(--text)" }}
            >
              You added {persona.name}, so you can remove it, with everything
              it keeps. This cannot be undone, and no copy is kept anywhere.
            </p>
            {!confirmDelete ? (
              <div className="mt-3">
                <Button
                  icon={mdiDelete}
                  label="Remove this app"
                  color="lightDark"
                  small
                  roundedFull
                  onClick={() => {
                    setConfirmDelete(true);
                    setTyped("");
                  }}
                />
              </div>
            ) : (
              <div className="mt-3">
                <label
                  className="text-sm"
                  htmlFor="persona-confirm"
                  style={{ fontFamily: NUNITO, color: "var(--text2)" }}
                >
                  Type <code>{id}</code> to confirm.
                </label>
                <input
                  id="persona-confirm"
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-2 w-full max-w-xs rounded-xl border border-[var(--line)] bg-[var(--card2)] px-3 py-2 font-mono text-sm text-[var(--text)]"
                />
                <div className="mt-2 flex gap-2">
                  <Button
                    label={deleting ? "Removing…" : "Remove for good"}
                    color="danger"
                    small
                    roundedFull
                    disabled={deleting || typed.trim() !== id}
                    onClick={removeThis}
                  />
                  <Button
                    label="Keep it"
                    color="lightDark"
                    small
                    roundedFull
                    disabled={deleting}
                    onClick={() => setConfirmDelete(false)}
                  />
                </div>
              </div>
            )}
          </Panel>
        </>
      )}
    </AppPage>
  );
}
