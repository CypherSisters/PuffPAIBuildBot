"use client";

/**
 * Build a persona — the dashboard half of `puffpai persona new`.
 *
 * WHAT THIS IS FOR
 * ----------------
 * The build interview is honest about a goal nothing ships for: "Nothing we
 * ship matches that yet." The useful next move is not a shopping list, it is a
 * blank app with the owner's own words in it — their lists, their name, its own
 * page. The CLI has written one since the scaffold landed. This is that, for
 * somebody who never opens a terminal.
 *
 * THE PREVIEW IS THE BACKEND'S, NOT OURS
 * --------------------------------------
 * "The words it will understand" is computed by POST /persona/preview, which
 * runs the same functions the write runs. Deriving it here — lowercase the
 * name, split the goal, drop the filler words — would be a second copy of rules
 * that live in Python, and the first time the two drifted this page would be
 * promising vocabulary the installed manifest does not carry. That is the exact
 * class of lie the whole feature exists to stop telling, so the preview costs a
 * round trip and is worth it.
 *
 * NOTHING IS WRITTEN UNTIL CREATE
 * -------------------------------
 * Preview writes nothing; the id is not reserved; a refusal is shown in the
 * backend's own words because those words were written for the owner. Create
 * scaffolds, rescans on the box, tells every /build/self reader that the build
 * changed (so the rail shows the new app without a page reload), and lands on
 * the app's own page.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { mdiClose, mdiPlus } from "@mdi/js";
import Icon from "../../../_components/Icon";
import {
  AppHeader,
  AppPage,
  FREDOKA,
  JBM,
  NUNITO,
  Panel,
  PanelTitle,
  Pill,
  SectionBar,
  inputStyle,
  microLabel,
  wellStyle,
} from "./ui";
import {
  personaProblem,
  previewPersona,
  scaffoldPersona,
  type PersonaPreview,
} from "@/app/_lib/services/personaService";
import { notifyBuildChanged } from "@/app/_lib/services/buildService";

/** Matches the backend's ceilings (persona_scaffold). Duplicated only as an
 *  input `maxLength`, which is a courtesy; the refusal is still the box's. */
const MAX_NAME = 40;
const MAX_LIST_NAME = 32;
const MAX_LISTS = 8;
const MAX_GOAL = 300;

/** How long to wait after a keystroke before asking the box what it would
 *  write. Long enough that typing a name is one request, not eleven. */
const PREVIEW_DEBOUNCE_MS = 400;

function Label({ children }: { children: React.ReactNode }) {
  return <div style={{ ...microLabel, marginBottom: 8 }}>{children}</div>;
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <p
      style={{
        margin: "6px 0 0",
        fontFamily: NUNITO,
        fontWeight: 500,
        fontSize: 13,
        lineHeight: 1.45,
        color: "var(--text3)",
      }}
    >
      {children}
    </p>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: "var(--chip)",
        borderRadius: 999,
        padding: "6px 12px",
        fontFamily: JBM,
        fontWeight: 700,
        fontSize: 12,
        color: "var(--text2)",
      }}
    >
      {children}
    </span>
  );
}

export default function PersonaBuilder() {
  const router = useRouter();

  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [lists, setLists] = useState<string[]>([]);
  const [listDraft, setListDraft] = useState("");

  const [preview, setPreview] = useState<PersonaPreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const addList = useCallback(() => {
    const value = listDraft.trim();
    if (!value) return;
    setLists((current) =>
      current.length >= MAX_LISTS ||
      current.some((entry) => entry.toLowerCase() === value.toLowerCase())
        ? current
        : [...current, value],
    );
    setListDraft("");
  }, [listDraft]);

  const dropList = (value: string) =>
    setLists((current) => current.filter((entry) => entry !== value));

  // Live preview. Cancelled on every change, and a stale answer is discarded by
  // sequence number rather than by time: two requests in flight must not be able
  // to paint in the wrong order.
  const seq = useRef(0);
  useEffect(() => {
    if (!name.trim()) {
      setPreview(null);
      setProblem(null);
      return;
    }
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      previewPersona({ name: name.trim(), goal: goal.trim(), lists })
        .then((data) => {
          if (seq.current !== mine) return;
          setPreview(data);
          setProblem(null);
        })
        .catch((err) => {
          if (seq.current !== mine) return;
          setPreview(null);
          setProblem(personaProblem(err));
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [goal, lists, name]);

  const create = useCallback(async () => {
    if (!name.trim() || creating) return;
    setCreating(true);
    try {
      const reply = await scaffoldPersona({
        id: preview?.id,
        name: name.trim(),
        goal: goal.trim(),
        lists,
      });
      // The box has already rescanned. Tell the rail and the apps index, which
      // are mounted above this page and would otherwise keep yesterday's list.
      notifyBuildChanged();
      router.push(reply.persona.route || `/dashboard/apps/${reply.persona.id}`);
    } catch (err) {
      setProblem(personaProblem(err));
      setCreating(false);
    }
  }, [creating, goal, lists, name, preview?.id, router]);

  const ready = Boolean(preview && !preview.taken && !creating);

  return (
    <AppPage>
      <AppHeader
        title="Build a persona"
        lede="Name it, say in one sentence what it is for, and name the lists you want it to keep. That is an app: it gets its own page, its own store on this box, and the words below start working in chat."
      >
        <Link href="/dashboard/apps" style={{ textDecoration: "none" }}>
          <Pill>Back to apps</Pill>
        </Link>
      </AppHeader>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ── what the owner says ── */}
        <Panel>
          <div>
            <Label>What is it called</Label>
            <input
              id="persona-name"
              value={name}
              maxLength={MAX_NAME}
              onChange={(event) => setName(event.target.value)}
              placeholder="GardenBot"
              style={inputStyle}
            />
            <Hint>
              {preview ? (
                <>
                  Its id will be <code>{preview.id}</code>, and its page{" "}
                  <code>{preview.route}</code>.
                </>
              ) : (
                <>Up to {MAX_NAME} characters. The id is made from it.</>
              )}
            </Hint>
          </div>

          <div style={{ marginTop: 18 }}>
            <Label>What is it for</Label>
            <textarea
              id="persona-goal"
              value={goal}
              rows={3}
              maxLength={MAX_GOAL}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="a garden and greenhouse assistant for a small family farm"
              style={{ ...inputStyle, resize: "vertical" }}
            />
            <Hint>
              One sentence. This is data: it becomes the line on the app&apos;s
              page and a few words for the router to match. It is never read as
              an instruction to the model.
            </Hint>
          </div>

          <div style={{ marginTop: 18 }}>
            <Label>Lists it should keep</Label>
            <div className="flex flex-wrap gap-2" style={{ marginBottom: 10 }}>
              {lists.map((entry) => (
                <Chip key={entry}>
                  {entry}
                  <button
                    type="button"
                    aria-label={`Remove the ${entry} list`}
                    onClick={() => dropList(entry)}
                    className="cursor-pointer border-0 bg-transparent p-0 leading-none"
                    style={{ color: "var(--text3)" }}
                  >
                    <Icon path={mdiClose} size="14" w="w-4" h="h-4" />
                  </button>
                </Chip>
              ))}
              {lists.length === 0 && (
                <span style={{ ...microLabel, letterSpacing: ".06em" }}>
                  none yet
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <input
                id="persona-list"
                value={listDraft}
                maxLength={MAX_LIST_NAME}
                onChange={(event) => setListDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addList();
                  }
                }}
                placeholder="plantings"
                style={inputStyle}
                disabled={lists.length >= MAX_LISTS}
              />
              <Pill
                onClick={addList}
                disabled={!listDraft.trim() || lists.length >= MAX_LISTS}
              >
                <Icon path={mdiPlus} size="16" w="w-4" h="h-4" />
                Add
              </Pill>
            </div>
            <Hint>
              {lists.length >= MAX_LISTS
                ? `${MAX_LISTS} lists is as many as one app carries.`
                : `Up to ${MAX_LISTS}, each up to ${MAX_LIST_NAME} characters. An app with no lists is still a valid app.`}
            </Hint>
          </div>
        </Panel>

        {/* ── what the box says it would write ── */}
        <Panel>
          <PanelTitle>What it will understand</PanelTitle>
          {!name.trim() && (
            <p
              style={{
                fontFamily: NUNITO,
                fontSize: 14,
                lineHeight: 1.5,
                color: "var(--text2)",
                margin: 0,
              }}
            >
              Give it a name and this fills in. Every word here comes from the
              box, from the same code that writes the file, so what you read is
              what gets installed.
            </p>
          )}

          {problem && (
            <p
              className="pl-3"
              style={{
                borderLeft: "3px solid var(--lemon)",
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 14,
                lineHeight: 1.45,
                color: "var(--lemon)",
                margin: 0,
              }}
            >
              {problem}
            </p>
          )}

          {preview && (
            <>
              {preview.taken && (
                <p
                  className="pl-3"
                  style={{
                    borderLeft: "3px solid var(--lemon)",
                    fontFamily: NUNITO,
                    fontWeight: 600,
                    fontSize: 14,
                    color: "var(--lemon)",
                    margin: "0 0 12px",
                  }}
                >
                  An app called {preview.id} is already installed. Pick another
                  name. Nothing of yours is overwritten.
                </p>
              )}

              <div style={wellStyle}>
                <div style={microLabel}>Say</div>
                <ul
                  style={{
                    margin: "8px 0 0",
                    padding: 0,
                    listStyle: "none",
                    fontFamily: NUNITO,
                    fontSize: 14,
                    lineHeight: 1.7,
                    color: "var(--text)",
                  }}
                >
                  {(preview.says.length > 0 ? preview.says : preview.examples)
                    .slice(0, 6)
                    .map((phrase) => (
                      <li key={phrase}>“{phrase}”</li>
                    ))}
                  {preview.says.length === 0 && preview.examples.length === 0 && (
                    <li style={{ color: "var(--text2)" }}>
                      Nothing yet. Name a list and these appear. An app with no
                      lists has nothing to be told.
                    </li>
                  )}
                </ul>
              </div>

              <div style={{ ...wellStyle, marginTop: 10 }}>
                <div style={microLabel}>Words the router will match</div>
                <div className="flex flex-wrap gap-1.5" style={{ marginTop: 8 }}>
                  {preview.keywords.map((word) => (
                    <Chip key={word}>{word}</Chip>
                  ))}
                </div>
                <Hint>
                  Filler is dropped, so “a bot for my small family” teaches it
                  nothing it would answer for wrongly.
                </Hint>
              </div>

              <div style={{ ...wellStyle, marginTop: 10 }}>
                <div style={microLabel}>Two files, both yours to edit</div>
                <p
                  style={{
                    margin: "8px 0 0",
                    fontFamily: JBM,
                    fontSize: 11,
                    lineHeight: 1.6,
                    color: "var(--text2)",
                    wordBreak: "break-all",
                  }}
                >
                  {preview.app_path}
                  <br />
                  {preview.pack_path}
                </p>
                <Hint>
                  Outside the install, so an update never touches them and your
                  data is not sitting in a source tree.
                </Hint>
              </div>
            </>
          )}
        </Panel>
      </div>

      <SectionBar title="Make it">
        <span style={{ fontFamily: JBM, fontSize: 11, color: "var(--text3)" }}>
          POST /persona/scaffold
        </span>
      </SectionBar>
      <Panel>
        <div className="flex flex-wrap items-center gap-3">
          <Pill tone="accent" onClick={create} disabled={!ready}>
            {creating ? "Writing…" : "Create it"}
          </Pill>
          <span
            style={{
              fontFamily: NUNITO,
              fontSize: 14,
              color: "var(--text2)",
            }}
          >
            {preview
              ? `It will appear in the sidebar and on the home board as ${preview.name}.`
              : "Nothing has been written yet."}
          </span>
        </div>
      </Panel>
    </AppPage>
  );
}
