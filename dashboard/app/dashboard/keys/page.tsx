"use client";

/**
 * Sign-up keys.
 *
 * One card per service this bot knows how to use with a free key, in the
 * backend's own words: what it is for, what it costs, the steps to get a key,
 * and a hidden box to paste it into. "Check and save" first shows the
 * backend's sentence and needs a "Yes, add it" for that sentence; the key is
 * sent once, in that one request, and the box is emptied before the reply
 * comes back. No screen shows a key again.
 *
 * The page re-reads everything from the backend after every action, so what
 * it shows is what the backend holds, never what the page assumed.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import {
  AppHeader,
  AppPage,
  JBM,
  NUNITO,
  Panel,
  inputStyle,
  wellStyle,
} from "@/app/dashboard/apps/_components/ui";
import {
  addKey,
  askToAdd,
  askToKeepWorkingWhenLocked,
  checkKey,
  getKeys,
  removeKey,
  safeSignupUrl,
  setKeepWorkingWhenLocked,
  type KeysPage,
  type SignUpKey,
} from "./keysService";

type Pending = {
  id: string;
  kind: "add" | "locked";
  ticket: string;
  sentence: string;
  background: boolean;
};

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

function StateChip({ item }: { item: SignUpKey }) {
  const works = item.state === "works";
  const text =
    item.state === "works"
      ? "Works"
      : item.state === "missing"
        ? "Not added"
        : item.state === "stopped_working"
          ? "Stopped"
          : "Paused";
  return (
    <span
      style={{
        fontFamily: JBM,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: ".08em",
        textTransform: "uppercase",
        padding: "3px 8px",
        borderRadius: 999,
        border: "1px solid var(--line)",
        background: works ? "var(--mint)" : "var(--card2)",
        color: works ? "var(--puff-on-accent)" : "var(--text3)",
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </span>
  );
}

function KeyCard({
  item,
  page,
  pending,
  busy,
  act,
  setPending,
}: {
  item: SignUpKey;
  page: KeysPage;
  pending: Pending | null;
  busy: boolean;
  act: (work: () => Promise<unknown>) => Promise<void>;
  setPending: (p: Pending | null) => void;
}) {
  // The pasted key lives only in this component's memory, and only until it
  // is sent or the owner says "Not now".
  const [value, setValue] = useState("");
  const [background, setBackground] = useState(false);
  const how = item.walk_through;
  const link = safeSignupUrl(how.signup_url);
  const needsKey = item.state === "missing" || item.state === "stopped_working";
  const canPaste = page.vault_set_up && page.vault_open;

  return (
    <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
      <div className="flex flex-wrap items-center gap-3">
        <StateChip item={item} />
        <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
          {item.name}
        </strong>
        <span style={{ ...small, color: "var(--text3)" }}>{item.state_line}</span>
        <div className="flex-1" />
        {!needsKey && (
          <>
            <Button label="Check now" color="info" small roundedFull
                    disabled={busy} onClick={() => act(() => checkKey(item.id))} />
            <Button label="Remove" color="lightDark" small roundedFull
                    disabled={busy} onClick={() => act(() => removeKey(item.id))} />
          </>
        )}
      </div>
      <p style={small}>For: {item.for}</p>
      <p style={small}>{item.cost_note}</p>
      {how.label && <p style={{ ...small, color: "var(--text)" }}>{how.label}</p>}
      {item.uses_line && <p style={small}>{item.uses_line}</p>}

      {!needsKey && !item.card_required && item.background_possible && (
        <div className="flex flex-wrap items-center gap-2">
          <p style={small}>
            {item.background
              ? "Keeps working while your vault is locked."
              : "Works only while your vault is open."}
          </p>
          <Button
            label={item.background ? "Only while open" : "Keep working when locked"}
            color="lightDark"
            small
            roundedFull
            disabled={busy}
            onClick={() =>
              act(async () => {
                if (item.background) {
                  await setKeepWorkingWhenLocked(item.id, false);
                } else {
                  const asked = await askToKeepWorkingWhenLocked(item.id);
                  setPending({
                    id: item.id,
                    kind: "locked",
                    ticket: asked.ticket,
                    sentence: asked.sentence,
                    background: true,
                  });
                }
              })
            }
          />
        </div>
      )}

      {needsKey && (
        <div style={{ display: "grid", gap: 8 }}>
          <p style={{ ...small, color: "var(--text)" }}>{how.line}</p>
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            {how.steps.map((step) => (
              <li key={step} style={small}>{step}</li>
            ))}
          </ol>
          <p style={small}>It takes {how.how_long}.</p>
          {link && (
            <div>
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                style={{ ...small, color: "var(--info, var(--text))", textDecoration: "underline" }}
              >
                Open {item.name}&apos;s sign-up page
              </a>
            </div>
          )}
          {!canPaste ? (
            <p style={{ ...small, color: "var(--text3)" }}>
              {page.vault_set_up
                ? "Your vault is locked. Open it on the Vault page, then paste the key here."
                : "A key needs the vault. Set your vault PIN in Setup first, then add the key."}
            </p>
          ) : (
            <>
              <label style={small} htmlFor={`key-${item.id}`}>
                Copy the key {item.name} shows you and paste it here
              </label>
              <input
                id={`key-${item.id}`}
                type="password"
                autoComplete="off"
                spellCheck={false}
                autoCapitalize="off"
                autoCorrect="off"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                style={{ ...inputStyle, fontFamily: JBM }}
              />
              {item.card_required ? (
                <p style={small}>
                  {item.name} asks for a payment card, so its key never works while
                  your vault is locked.
                </p>
              ) : (
                item.background_possible && (
                  <label style={{ ...small, display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={background}
                      onChange={(e) => setBackground(e.target.checked)}
                    />
                    Keep working when the vault is locked. Anyone who gets into this
                    computer account could use this key.
                  </label>
                )
              )}
              {!pending && (
                <div>
                  <Button
                    label="Check and save"
                    color="info"
                    small
                    roundedFull
                    disabled={busy || value.trim().length === 0}
                    onClick={() =>
                      act(async () => {
                        const asked = await askToAdd(item.id, background);
                        setPending({
                          id: item.id,
                          kind: "add",
                          ticket: asked.ticket,
                          sentence: asked.sentence,
                          background,
                        });
                      })
                    }
                  />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {pending && (
        <div style={{ borderLeft: "3px solid var(--lemon)", paddingLeft: 10, display: "grid", gap: 8 }}>
          <p style={{ ...small, color: "var(--text)" }}>{pending.sentence}</p>
          <div className="flex gap-2">
            <Button
              label={pending.kind === "add" ? "Yes, add it" : "Yes, keep it working"}
              color="info"
              small
              roundedFull
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const yes = pending;
                  setPending(null);
                  if (yes.kind === "add") {
                    const sent = value;
                    setValue(""); // emptied before the reply comes back
                    return addKey(yes.id, sent, yes.ticket, yes.background);
                  }
                  return setKeepWorkingWhenLocked(yes.id, true, yes.ticket);
                })
              }
            />
            <Button
              label="Not now"
              color="lightDark"
              small
              roundedFull
              disabled={busy}
              onClick={() => {
                setPending(null);
                setValue("");
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default function KeysPageView() {
  const [page, setPage] = useState<KeysPage | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPage(await getKeys());
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "This page did not load.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = useCallback(
    async (work: () => Promise<unknown>) => {
      setBusy(true);
      setProblem(null);
      setSaid(null);
      try {
        const result = (await work()) as { message?: string; notes?: string[] } | undefined;
        if (result && typeof result.message === "string") {
          setSaid([result.message, ...(result.notes || [])].join(" "));
        }
      } catch (err) {
        setProblem(err instanceof Error ? err.message : "That did not work.");
      } finally {
        await load();
        setBusy(false);
      }
    },
    [load],
  );

  if (!page) {
    return (
      <AppPage>
        <AppHeader title="Sign-up keys" />
        {problem && <p style={small}>{problem}</p>}
      </AppPage>
    );
  }

  return (
    <AppPage>
      <AppHeader title={page.title} lede={page.line} />
      {problem && (
        <Panel accent="--lemon">
          <p style={small}>{problem}</p>
        </Panel>
      )}
      {said && (
        <Panel accent="--mint">
          <p style={small}>{said}</p>
        </Panel>
      )}
      <div style={{ display: "grid", gap: 10 }}>
        {page.keys.map((item) => (
          <KeyCard
            key={item.id}
            item={item}
            page={page}
            busy={busy}
            act={act}
            pending={pending && pending.id === item.id ? pending : null}
            setPending={setPending}
          />
        ))}
      </div>
      {/* The voice key is shown on its own page,
          reached from here as part of the key walkthrough. */}
      <Panel accent="--mint">
        <div style={{ display: "grid", gap: 6 }}>
          <p style={{ ...small, color: "var(--text)", fontWeight: 700 }}>
            Home Assistant voice key
          </p>
          <p style={small}>
            To let Home Assistant voice use your apps, give Home Assistant its voice key.
          </p>
          <div>
            <a
              href="/dashboard/keys/voice"
              style={{ ...small, color: "var(--info, var(--text))", textDecoration: "underline" }}
            >
              See or make the voice key
            </a>
          </div>
        </div>
      </Panel>
      <p style={{ ...small, color: "var(--text3)" }}>
        Never paste a key into the chat: chats are saved on this computer.
      </p>
    </AppPage>
  );
}
