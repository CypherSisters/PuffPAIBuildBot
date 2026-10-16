"use client";

/**
 * The build wizard in the browser — the same conversation as `puffpai setup`.
 *
 * First what this
 * computer has and the thinking model that fits it ("Let's see what we're
 * working with here"), then the goal, then a name suggested from it, then an
 * age that only changes the wording; then ONE plain summary with every download size and every outside
 * service, one "Looks good", and "Change something" to open any item; then the
 * vault PIN; then the build. The terminal and this page are equal: every
 * question and every sentence of the plan comes from the backend (/wizard/flow,
 * /wizard/summary), so the two cannot drift apart. This replaces the old
 * 8-step onboarding and the card review screen.
 *
 * SECRETS. The vault PIN and the recovery code live in this component's state
 * only, are cleared the moment they are used, and are never written to
 * localStorage, the URL or the console.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getTour, tourAgain } from "@/app/_lib/services/tourService";
import ConnectHomeAssistant from "@/app/_components/ToolServers/ConnectHomeAssistant";
import {
  applyWizardCard,
  approveSummary,
  chooseFirstLook,
  getFirstLook,
  chooseNeeds,
  getBuild,
  getFlow,
  getSummary,
  getWizardState,
  resumeWizard,
  skipVault,
  startBuild,
  type CardField,
  type FirstLook,
  type FlowReply,
  type ResumeOffer,
  type BuildWaiting,
  type Summary,
  type SummaryChanges,
  type SummaryItem,
  type WizardCard,
} from "../_lib/services/wizardService";
import { createVault } from "../_lib/services/setupService";
import {
  RECOVERY_CONFIRM_FAILED,
  RECOVERY_NOT_CONFIRMED,
  recoveryCodeMatches,
} from "../_lib/recoveryCode";
import { Card, Choice, ConnectedTo, FactRow, Notice, PuffButton, Toggle, inputStyle } from "./ui";
import NeedsStep from "./NeedsStep";
import FindStep from "./FindStep";
import GitHubStep from "./GitHubStep";
import { githubDue, toolsmithDue } from "./githubDue";
import ToolsmithStep from "./ToolsmithStep";
import VaultWhere from "./VaultWhere";
import DataFolderChooser from "@/app/_components/MyData/DataFolderChooser";
import PlanGroups, { PlanNotes } from "./PlanGroups";
import BuildingLines, { keepOnScreen } from "./BuildingLines";
import VaultWaitUnlock from "@/app/_components/Jobs/VaultWaitUnlock";
import AssembleYesCards from "@/app/_components/Assemble/AssembleYesCards";
import BuildHandoff from "./BuildHandoff";
import { FieldRenderer, answersToSend, initialAnswers, type AnswerValue } from "./review/fields";
import {
  BRAIN_OFF,
  choicePage,
  emptyChanges,
  isBrain,
  numbered,
  offLine,
  offLineShort,
  detailWhenOff,
  pinRefusal,
  townOnLine,
  withBrain,
  withReach,
  withTick,
  withTown,
} from "./summaryModel";

const H1 = { color: "var(--text)", fontSize: 24, fontWeight: 800, margin: 0, lineHeight: 1.25 };
const QUESTION = { color: "var(--text)", fontSize: 18, fontWeight: 700, margin: "18px 0 4px" };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "0 0 8px" };
const QUIET = { color: "var(--text3)", fontSize: 13, lineHeight: 1.5 };

function cardIdOf(card: WizardCard): string {
  return card.part ? `${card.skill}:${card.part}` : card.skill;
}

function Problem({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div role="alert" style={{ color: "var(--puff-red)", fontSize: 14, marginTop: 8 }}>
      {text}
    </div>
  );
}

// ── a question ───────────────────────────────────────────────────────────────

function Question({ card, onDone }: { card: WizardCard; onDone: () => void }) {
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>(() =>
    initialAnswers(card.fields),
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);

  useEffect(() => {
    first.current?.focus();
  }, []);

  const set = (id: string, value: AnswerValue) =>
    setAnswers((prev) => ({ ...prev, [id]: value }));

  async function submit(event?: React.FormEvent) {
    event?.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    // An empty text box is "keep what is there", exactly like pressing Enter
    // on the terminal — never an answer of nothing.
    const sent = answersToSend(card.fields, answers);
    for (const field of card.fields) {
      if (field.kind === "text" && sent[field.id] === "") delete sent[field.id];
    }
    const reply = await applyWizardCard(cardIdOf(card), sent);
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(
        reply.status === 0
          ? "No answer from this computer. Nothing was changed."
          : reply.data?.message || "That was not accepted. Nothing was changed.",
      );
      return;
    }
    setAnswers({});
    onDone();
  }

  const askable = card.fields.filter((field) => field.kind !== "info");
  return (
    <form onSubmit={submit}>
      <h1 style={H1}>{card.title}</h1>
      {card.fields
        .filter((field) => field.kind === "info")
        .map((field) => (
          <p key={field.id} style={HELP}>
            {field.label}
            {field.current ? `: ${String(field.current)}` : ""}
          </p>
        ))}
      {askable.map((field, index) => (
        <QuestionField
          key={field.id}
          field={field}
          value={answers[field.id]}
          onChange={(next) => set(field.id, next)}
          inputRef={index === 0 ? first : undefined}
        />
      ))}
      <Problem text={error} />
      <div style={{ marginTop: 18 }}>
        <PuffButton kind="primary" type="submit" label="Next" disabled={busy} />
      </div>
    </form>
  );
}

function QuestionField({
  field,
  value,
  onChange,
  inputRef,
}: {
  field: CardField;
  value: AnswerValue | undefined;
  onChange: (next: AnswerValue) => void;
  inputRef?: React.MutableRefObject<HTMLTextAreaElement | HTMLInputElement | null>;
}) {
  const id = `q-${field.id}`;
  if (field.kind !== "text") {
    return (
      <div style={{ marginTop: 14 }}>
        <FieldRenderer field={field} value={value} onChange={onChange} />
      </div>
    );
  }
  const long = (field.max_length ?? 0) > 400;
  return (
    <div>
      <label htmlFor={id} style={{ ...QUESTION, display: "block" }}>
        {field.label}
      </label>
      {field.help ? <p style={HELP}>{field.help}</p> : null}
      {long ? (
        <textarea
          id={id}
          ref={inputRef as React.MutableRefObject<HTMLTextAreaElement | null>}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
          maxLength={field.max_length}
          rows={3}
          style={{ ...inputStyle, resize: "vertical" }}
        />
      ) : (
        <input
          id={id}
          ref={inputRef as React.MutableRefObject<HTMLInputElement | null>}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
          maxLength={field.max_length}
          autoComplete="off"
          style={inputStyle}
        />
      )}
      {field.examples && field.examples.length > 0 && (
        // Sample answers from the backend (the goal's three sample goals).
        // A tap puts one in the box, to send as it is or change first.
        <div style={{ marginTop: 10, display: "grid", gap: 6 }} data-examples={field.id}>
          <p style={{ ...HELP, margin: 0 }}>Or tap one to start from it:</p>
          {field.examples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                onChange(example);
                inputRef?.current?.focus();
              }}
              style={{
                textAlign: "left",
                cursor: "pointer",
                padding: "8px 12px",
                borderRadius: 12,
                border: "1px solid var(--line)",
                background: value === example ? "var(--card2)" : "transparent",
                color: "var(--text)",
                fontSize: 14,
                lineHeight: 1.4,
                fontFamily: "inherit",
              }}
            >
              {example}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── the first screen: this computer, and the thinking model that fits ────────

function FirstLookStep({ onDone }: { onDone: () => void }) {
  const [look, setLook] = useState<FirstLook | null>(null);
  const [changing, setChanging] = useState(false);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      const reply = await getFirstLook();
      if (!reply.ok || !reply.data?.found) {
        setError(
          reply.status === 0
            ? "No answer from this computer."
            : reply.data?.message || "This computer could not be checked.",
        );
        return;
      }
      setLook(reply.data);
      setPicked(reply.data.current || reply.data.recommend.value);
    })();
  }, []);

  async function use(model?: string) {
    setBusy(true);
    const reply = await chooseFirstLook(model);
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(reply.data?.message || "That was not accepted. Nothing was changed.");
      return;
    }
    onDone();
  }

  if (!look) {
    return (
      <div>
        <p style={HELP}>Checking this computer...</p>
        <Problem text={error} />
      </div>
    );
  }
  const others = look.choices.filter((choice) => !choice.recommended);
  return (
    <div>
      <h1 style={H1}>{look.title}</h1>
      <p style={HELP}>{look.intro}</p>
      <div style={{ margin: "10px 0 14px" }}>
        {look.found.map((row) => (
          <FactRow key={row.label} label={row.label} value={row.text} />
        ))}
      </div>
      {look.recommend.value ? (
        <Notice>
          <div style={{ color: "var(--text)", fontWeight: 800 }}>{look.recommend.line}</div>
          <div style={{ marginTop: 4 }}>{look.recommend.why}</div>
          <div style={{ marginTop: 4 }}>{look.recommend.good_for}</div>
        </Notice>
      ) : null}
      {look.none_line ? <p style={HELP}>{look.none_line}</p> : null}
      {changing ? (
        <section style={{ marginTop: 12 }}>
          {look.choices.map((choice) => (
            <Choice
              key={choice.value}
              name="first-look-model"
              value={choice.value}
              checked={picked === choice.value}
              onSelect={setPicked}
              title={choice.label}
              description={choice.good_for}
            />
          ))}
        </section>
      ) : others.length ? (
        <section style={{ marginTop: 12 }}>
          <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 15 }}>{look.others_heading}</div>
          {others.map((choice) => (
            <div key={choice.value} style={{ ...HELP, margin: "4px 0 0" }}>
              {choice.label}; {choice.good_for}
            </div>
          ))}
        </section>
      ) : null}
      {look.too_big ? <p style={{ ...QUIET, marginTop: 10 }}>{look.too_big}</p> : null}
      <Problem text={error} />
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        {changing ? (
          <PuffButton kind="primary" label="Use this one" onClick={() => void use(picked)} disabled={busy} />
        ) : (
          <PuffButton
            kind="primary"
            label={look.actions[0]?.label ?? "Use this one"}
            onClick={() => void use()}
            disabled={busy || !look.recommend.value}
          />
        )}
        {look.choices.length > 1 && !changing ? (
          <PuffButton label={look.actions[1]?.label ?? "Change"} onClick={() => setChanging(true)} disabled={busy} />
        ) : null}
        {!look.recommend.value ? (
          <PuffButton label="Go on" onClick={() => void use()} disabled={busy} />
        ) : null}
      </div>
    </div>
  );
}

// ── the one summary ──────────────────────────────────────────────────────────

function ItemRow({
  item,
  number,
  changing,
  summary,
  changes,
  onChange,
  town,
  onTown,
}: {
  item: SummaryItem;
  number: number | null;
  changing: boolean;
  summary: Summary;
  changes: SummaryChanges;
  onChange: (next: SummaryChanges) => void;
  town: string;
  onTown: (value: string) => void;
}) {
  const [page, setPage] = useState(0);
  const head = number ? `${number}. ` : "";
  if (changing && number && isBrain(item)) {
    const choices = [
      ...summary.model_choices,
      { value: BRAIN_OFF, label: summary.brain_off_label || "Not now" },
    ];
    const { shown, more } = choicePage(choices, summary.mode, page);
    const current = item.on ? changes.model ?? item.apply.ref : BRAIN_OFF;
    return (
      <div style={{ marginTop: 10 }}>
        <div style={{ color: "var(--text)", fontWeight: 700 }}>{head}{item.label}</div>
        {shown.map((choice) => (
          <Choice
            key={choice.value}
            name={`brain-${item.id}`}
            value={choice.value}
            checked={current === choice.value}
            onSelect={(value) => onChange(withBrain(changes, item, value))}
            title={choice.label}
          />
        ))}
        {more ? (
          <div style={{ marginTop: 8 }}>
            <PuffButton label="Show more" onClick={() => setPage(page + 1)} />
          </div>
        ) : null}
      </div>
    );
  }
  if (number) {
    // Every line that can be switched shows its tick box at all times,
    // so she can untick what she does not want to download.
    return (
      <div>
        <Toggle
          id={`item-${item.id}`}
          checked={item.on}
          onChange={(next) => onChange(withTick(summary, changes, item, next))}
          title={`${head}${item.label}`}
          description={item.on ? item.detail : <OffText item={item} short />}
        />
        {townOnLine(summary, item) && summary.town ? (
          <TownField summary={summary} town={town} onTown={onTown} />
        ) : null}
      </div>
    );
  }
  if (!item.on) {
    return (
      <div style={{ ...HELP, margin: "6px 0 0" }}>
        {head}
        <OffText item={item} />
      </div>
    );
  }
  return (
    <div style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}>
      <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 15 }}>
        {head}
        {item.label}
      </div>
      {item.detail ? <div style={{ ...HELP, margin: "2px 0 0" }}>{item.detail}</div> : null}
      {townOnLine(summary, item) && summary.town ? (
        <TownField summary={summary} town={town} onTown={onTown} />
      ) : null}
    </div>
  );
}

/** What an unticked line says: "Not included (you can add it): ...", and for
 *  a find made for the owner's jobs, what it is under that. */
function OffText({ item, short = false }: { item: SummaryItem; short?: boolean }) {
  const line = short ? offLineShort(item) : offLine(item);
  if (!detailWhenOff(item)) return <>{line}</>;
  return (
    <>
      <span style={{ display: "block" }}>{line}</span>
      <span style={{ display: "block" }} data-testid="off-detail">
        {item.detail}
      </span>
    </>
  );
}

function TownField({
  summary,
  town,
  onTown,
}: {
  summary: Summary;
  town: string;
  onTown: (value: string) => void;
}) {
  if (!summary.town) return null;
  return (
    <div style={{ marginTop: 6 }} data-testid="plan-town">
      <label
        htmlFor="plan-town-input"
        style={{ color: "var(--text)", fontWeight: 700, fontSize: 14, display: "block" }}
      >
        {summary.town.question}
      </label>
      <input
        id="plan-town-input"
        aria-label="Your town"
        value={town}
        maxLength={100}
        placeholder={summary.town.placeholder}
        onChange={(e) => onTown(e.target.value)}
        className="rounded-lg px-2 py-1"
        style={{
          marginTop: 4,
          width: "min(100%, 22rem)",
          color: "var(--text)",
          background: "var(--chip)",
          border: "1px solid var(--line)",
          fontSize: 14,
        }}
      />
      <div style={{ ...QUIET, marginTop: 4 }}>
        {summary.town.help} {summary.town.skip}
      </div>
    </div>
  );
}

function SummaryStep({ onApproved }: { onApproved: () => void }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [changes, setChanges] = useState<SummaryChanges>(emptyChanges());
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  // Redrawing the plan after a tick is not "busy": "Looks good" sends her
  // choices as they are, so a press right after unticking still counts.
  const [drawing, setDrawing] = useState(false);
  const [error, setError] = useState("");
  const [reachPage, setReachPage] = useState(0);
  // Kept apart from `changes`: typing a town does not redraw the plan.
  const [town, setTown] = useState("");

  const load = useCallback(async (next: SummaryChanges) => {
    setDrawing(true);
    const reply = await getSummary(next);
    setDrawing(false);
    if (!reply.ok || !reply.data?.sections) {
      setError(
        reply.status === 0
          ? "No answer from this computer."
          : reply.data?.message || "The plan could not be drawn.",
      );
      return;
    }
    setError("");
    setSummary(reply.data);
  }, []);

  useEffect(() => {
    void load(emptyChanges());
  }, [load]);

  const change = (next: SummaryChanges) => {
    setChanges(next);
    void load(next);
  };

  // "Similar projects also have": the ticked line becomes a job, then the
  // plan is drawn again with its finds under it.
  async function addSimilar(name: string) {
    setBusy(true);
    const reply = await chooseNeeds({ added: [name] });
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(reply.data?.message || "That was not accepted. Nothing was changed.");
      return;
    }
    await load(changes);
  }

  async function looksGood() {
    setBusy(true);
    const reply = await approveSummary(summary ? withTown(changes, summary, town) : changes);
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(reply.data?.message || "That was not accepted. Nothing was changed.");
      return;
    }
    onApproved();
  }

  if (!summary) {
    return (
      <div>
        <p style={HELP}>Putting your plan together...</p>
        <Problem text={error} />
      </div>
    );
  }
  const numbers = new Map(numbered(summary).map((item, i) => [item.id, i + 1]));
  const reachNumber = numbers.size + 1;
  const reach = choicePage(summary.reach.choices, summary.mode, reachPage);
  const row = (item: SummaryItem) => (
    <ItemRow
      key={item.id}
      item={item}
      number={numbers.get(item.id) ?? null}
      changing={changing}
      summary={summary}
      changes={changes}
      onChange={change}
      town={town}
      onTown={setTown}
    />
  );
  return (
    <div>
      {(summary.checked ?? []).map((line) => (
        <p key={line} style={{ ...QUIET, margin: 0 }}>
          {line}
        </p>
      ))}
      <h1 style={{ ...H1, marginTop: 14 }}>{summary.title}</h1>
      <p style={HELP}>{summary.intro}</p>
      <PlanGroups
        summary={summary}
        row={row}
        busy={busy || drawing}
        onAddSimilar={(name) => void addSimilar(name)}
      />
      {summary.sections.map((section) => (
        <section key={section.id} style={{ marginTop: 16 }}>
          <h2 style={{ color: "var(--text)", fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
            {section.heading}
          </h2>
          {section.items.length === 0 && !section.notes?.length && section.empty ? (
            <div style={QUIET}>{section.empty}</div>
          ) : null}
          {section.items.map((item) => row(item))}
          <PlanNotes notes={section.notes} />
        </section>
      ))}
      <section style={{ marginTop: 16 }}>
        {changing ? (
          <div>
            <div style={{ color: "var(--text)", fontWeight: 700 }}>
              {reachNumber}. {summary.reach.label}
            </div>
            {reach.shown.map((choice) => (
              <Choice
                key={choice.value}
                name="reach"
                value={choice.value}
                checked={summary.reach.current === choice.value}
                onSelect={(value) => change(withReach(changes, value))}
                title={choice.label}
              />
            ))}
            {reach.more ? (
              <div style={{ marginTop: 8 }}>
                <PuffButton label="Show more" onClick={() => setReachPage(reachPage + 1)} />
              </div>
            ) : null}
          </div>
        ) : (
          <div style={{ color: "var(--text)", fontWeight: 700 }}>
            {reachNumber}. {summary.reach.label}
          </div>
        )}
      </section>
      {/* The knowledge part of the plan: one folder for downloads and her own files. */}
      <section style={{ marginTop: 16 }}>
        <DataFolderChooser />
      </section>
      <Notice>
        <div>{summary.lines.total}</div>
        <div>{summary.lines.leaves}</div>
      </Notice>
      <Problem text={error} />
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        <PuffButton
          kind="primary"
          label={summary.actions[0]?.label ?? "Looks good"}
          onClick={() => void looksGood()}
          disabled={busy}
        />
        <PuffButton
          label={changing ? "Done changing" : summary.actions[1]?.label ?? "Change something"}
          onClick={() => setChanging(!changing)}
          disabled={busy}
        />
      </div>
    </div>
  );
}

// ── the vault ────────────────────────────────────────────────────────────────

function VaultStep({ flow, onDone }: { flow: FlowReply; onDone: () => void }) {
  const [pin, setPin] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState("");
  const [typed, setTyped] = useState("");
  // Where it lives is asked first; an older backend sends no
  // question, and the PIN is asked straight away as before.
  const [placed, setPlaced] = useState<string[] | null>(flow.vault?.where ? null : []);

  // Nothing secret outlives this screen.
  useEffect(
    () => () => {
      setPin("");
      setAgain("");
      setCode("");
      setTyped("");
    },
    [],
  );

  async function lock(event?: React.FormEvent) {
    event?.preventDefault();
    const refusal = pinRefusal(pin);
    if (refusal) {
      setError(refusal);
      return;
    }
    if (pin !== again) {
      setError("The two PINs are not the same. Type them again.");
      return;
    }
    setBusy(true);
    const reply = await createVault(pin);
    setBusy(false);
    setPin("");
    setAgain("");
    if (reply.status === 409 || reply.data?.already_initialized) {
      onDone();
      return;
    }
    if (!reply.ok || !reply.data?.success) {
      setError(reply.data?.message || "The vault was not made. Nothing was changed.");
      return;
    }
    setError("");
    if (reply.data.recovery_code) {
      setCode(reply.data.recovery_code);
      setNotice(reply.data.recovery_code_notice || "");
      return;
    }
    onDone();
  }

  function confirm() {
    if (!recoveryCodeMatches(code, typed)) {
      setError(RECOVERY_CONFIRM_FAILED);
      return;
    }
    setCode("");
    setTyped("");
    onDone();
  }

  function cannotNow() {
    setCode("");
    setTyped("");
    setError("");
    window.alert(RECOVERY_NOT_CONFIRMED);
    onDone();
  }

  async function later() {
    await skipVault();
    onDone();
  }

  const vault = flow.vault;
  if (placed === null && vault?.where) {
    return (
      <div>
        <h1 style={H1}>{vault.title}</h1>
        <VaultWhere
          words={vault.where}
          onPlaced={(lines) => setPlaced(lines)}
          onExisting={() => onDone()}
        />
        <div className="flex flex-wrap gap-2" style={{ marginTop: 10 }}>
          <PuffButton label="Set it later" onClick={() => void later()} />
        </div>
      </div>
    );
  }
  if (code) {
    return (
      <div>
        <h1 style={H1}>{vault?.title}</h1>
        <p style={HELP}>{notice}</p>
        <div
          style={{
            fontFamily: "var(--font-jbmono), monospace",
            fontSize: 22,
            letterSpacing: ".08em",
            color: "var(--text)",
            margin: "12px 0",
          }}
        >
          {code}
        </div>
        <label htmlFor="typed-back" style={{ ...QUESTION, display: "block" }}>
          Type the recovery code back in. Capitals and dashes do not matter.
        </label>
        <input
          id="typed-back"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          style={inputStyle}
        />
        <Problem text={error} />
        <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
          <PuffButton kind="primary" label="I wrote it down" onClick={confirm} />
          <PuffButton label="I cannot write it down now" onClick={cannotNow} />
        </div>
      </div>
    );
  }
  return (
    <form onSubmit={lock}>
      <h1 style={H1}>{vault?.title}</h1>
      <p style={HELP}>{vault?.what}</p>
      {placed && placed.length > 0 && (
        <Notice tone="ok">
          {placed.map((line) => (
            <span key={line} style={{ display: "block" }}>
              {line}
            </span>
          ))}
        </Notice>
      )}
      <label htmlFor="pin" style={{ ...QUESTION, display: "block" }}>
        {vault?.pin}
      </label>
      <p style={HELP}>{vault?.pin_help} What you type stays hidden.</p>
      <input
        id="pin"
        type="password"
        value={pin}
        onChange={(e) => setPin(e.target.value)}
        autoComplete="new-password"
        style={inputStyle}
      />
      <label htmlFor="pin-again" style={{ ...QUESTION, display: "block" }}>
        {vault?.again}
      </label>
      <p style={HELP}>{vault?.again_help}</p>
      <input
        id="pin-again"
        type="password"
        value={again}
        onChange={(e) => setAgain(e.target.value)}
        autoComplete="new-password"
        style={inputStyle}
      />
      <Problem text={error} />
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        <PuffButton kind="primary" type="submit" label="Lock it" disabled={busy} />
        <PuffButton label="Set it later" onClick={() => void later()} disabled={busy} />
      </div>
    </form>
  );
}

// ── building, and the last screen ────────────────────────────────────────────

function Building({ flow, onDone }: { flow: FlowReply; onDone: () => void }) {
  const [error, setError] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  // What the build left waiting for her answer: the screen stays
  // until each is answered, or she goes on without them.
  const [waiting, setWaiting] = useState<BuildWaiting[]>([]);
  const [goOn, setGoOn] = useState("");
  const [finished, setFinished] = useState(false);
  // Toolsmith waits for the vault: her PIN right here, not on the Vault page.
  const [vaultWait, setVaultWait] = useState("");
  const started = useRef(false);

  useEffect(() => {
    let stop = false;
    async function go() {
      if (flow.next === "build" && !started.current) {
        started.current = true;
        const reply = await startBuild();
        if (!reply.ok && reply.status !== 409) {
          setError(reply.data?.message || "The build did not start.");
          return;
        }
      }
      while (!stop) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const status = await getBuild();
        // What the build is doing, line by line, as it happens.
        if (status.ok && status.data.lines) setLines(status.data.lines);
        if (status.ok) setVaultWait(status.data.vault_wait || "");
        if (status.ok && !status.data.running) {
          if (status.data.error) setError(status.data.error);
          const left = status.data.waiting ?? [];
          if (left.length || keepOnScreen(status.data.lines ?? [])) {
            setWaiting(left);
            setGoOn(status.data.go_on || "Go on without them for now");
            setFinished(true);
            return;
          }
          onDone();
          return;
        }
      }
    }
    void go();
    return () => {
      stop = true;
    };
  }, [flow.next, onDone]);

  const [done, setDone] = useState<string[]>([]);
  const answered = (id: string) => setDone((ids) => (ids.includes(id) ? ids : [...ids, id]));
  // Once every held thing has her answer, the button no longer says "without them".
  const allAnswered = waiting.every((row) => row.status !== "held" || done.includes(row.id));

  return (
    <div>
      <h1 style={H1}>{flow.building_line || "Building your bot."}</h1>
      {finished ? null : <p style={HELP}>This page moves on by itself when it is done.</p>}
      {finished ? null : (
        <div style={{ marginTop: 10 }}>
          <VaultWaitUnlock line={vaultWait} />
        </div>
      )}
      <BuildingLines lines={lines} waiting={waiting} onAnswered={answered} />
      {finished ? (
        <div style={{ marginTop: 16 }}>
          <PuffButton label={allAnswered ? "Go on" : goOn} onClick={onDone} />
        </div>
      ) : null}
      <Problem text={error} />
    </div>
  );
}

/** "Next time: puffpai start, then http://127.0.0.1:3003/ in your web
 *  browser." from the handoff card's own lines (one line, not a block). */
function handoffNote(card: WizardCard): string {
  const value = (id: string) => String(card.fields.find((f) => f.id === id)?.current ?? "").trim();
  const start = value("start_0");
  const opens = value("dashboard");
  if (!start && !opens) return "";
  return `Next time: ${[start, opens].filter(Boolean).join(", then ")}.`;
}

function Done({ card }: { card: WizardCard }) {
  // Optional: Home Assistant's own tool server, so the house works in chat.
  const [showHa, setShowHa] = useState(false);
  return (
    <div>
      {/* A bot with jobs: its title says it is ready to build until her Yes,
           then How per job, the one setup list and one Yes (BuildHandoff).
           Without jobs, the card's title, the gaps and Start cards it had. */}
      <BuildHandoff
        title={card.title}
        heading={(text) => <h1 style={H1}>{text}</h1>}
        note={handoffNote(card)}
        intro={card.fields.map((field) => (
          <p key={field.id} style={{ ...HELP, margin: "6px 0 0" }}>
            <span style={{ color: "var(--text)", fontWeight: 700 }}>{field.label}: </span>
            {String(field.current ?? "")}
          </p>
        ))}
        fallback={
          <>
          {card.gaps.length ? (
            <section style={{ marginTop: 14 }}>
              {card.gaps.map((gap, i) => (
                <div key={`${gap.what}-${i}`} style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}>
                  <div style={{ color: "var(--text)", fontWeight: 700, fontSize: 14 }}>
                    Still to do: {gap.what}
                  </div>
                  <div style={{ ...HELP, margin: 0 }}>{gap.why}</div>
                  {gap.fix_command ? (
                    <code style={{ fontFamily: "var(--font-jbmono), monospace", fontSize: 12, color: "var(--text2)" }}>
                      {gap.fix_command}
                    </code>
                  ) : null}
                </div>
              ))}
            </section>
          ) : null}
          <AssembleYesCards />
          </>
        }
      />
      <section style={{ marginTop: 18 }}>
        {showHa ? (
          <ConnectHomeAssistant />
        ) : (
          <button
            type="button"
            onClick={() => setShowHa(true)}
            style={{ ...HELP, background: "none", border: 0, padding: 0, cursor: "pointer", color: "var(--text)", fontWeight: 700 }}
          >
            Have Home Assistant? Connect my Home Assistant (you can skip this)
          </button>
        )}
      </section>
      <div style={{ marginTop: 18 }}>
        <Link href="/dashboard" style={{ color: "var(--pink)", fontWeight: 700 }}>
          Open the dashboard
        </Link>
      </div>
    </div>
  );
}

// ── a setup that stopped part-way ────────────────────────────────────────────

/** "12 minutes ago" -- the same words `render_cli.how_long_ago` uses. */
function howLongAgo(seconds: number | undefined): string {
  const total = Math.max(0, Number(seconds) || 0);
  for (const [size, unit] of [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ] as const) {
    if (total >= size) {
      const count = Math.floor(total / size);
      return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
    }
  }
  return "a moment ago";
}

/** The same one screen `puffpai setup` shows before its first question. The
 *  browser decides nothing: continuing re-checks the vault and the brain on
 *  the server (POST /wizard/resume), and starting over keeps the old record. */
function Resume({ offer, onChosen }: { offer: ResumeOffer; onChosen: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function chooseResume(choice: "continue" | "restart") {
    setBusy(true);
    const reply = await resumeWizard(choice);
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(reply.data?.message || "That did not work. Nothing was changed.");
      return;
    }
    onChosen();
  }
  return (
    <div>
      <h1 style={H1}>
        A setup from {howLongAgo(offer.age_s)} stopped at: {offer.stopped_title || "a step"}.
      </h1>
      <p style={HELP}>Pick up where it stopped, or start over?</p>
      {(offer.decided ?? []).length ? (
        <div style={QUIET}>
          Already answered: {(offer.decided ?? []).map((row) => row.title).filter(Boolean).join(", ")}
        </div>
      ) : null}
      <p style={QUIET}>
        Picking up checks the vault and the brain on this computer again, and asks again if
        they are not there. Starting over keeps a copy of the old record.
      </p>
      <Problem text={error} />
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        <PuffButton kind="primary" label="Pick up where it stopped" onClick={() => void chooseResume("continue")} disabled={busy} />
        <PuffButton label="Start over" onClick={() => void chooseResume("restart")} disabled={busy} />
      </div>
    </div>
  );
}

// ── "Show me around again": the welcome tour, once more ─────────────────────

function ShowAround() {
  const router = useRouter();
  const [built, setBuilt] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void getTour().then((got) => setBuilt(!!got && got.built));
  }, []);
  if (!built) return null;
  return (
    <div style={{ marginTop: 12, textAlign: "center" }}>
      <PuffButton
        label="Show me around again"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void tourAgain().then(() => router.push("/dashboard"));
        }}
      />
    </div>
  );
}

// ── the page ─────────────────────────────────────────────────────────────────

export default function Conversation() {
  const [flow, setFlow] = useState<FlowReply | null>(null);
  const [error, setError] = useState("");
  const [resume, setResume] = useState<ResumeOffer | null>(null);
  const [asked, setAsked] = useState(false);
  // The GitHub question was answered on this page: never shown twice, even
  // when the backend could not record it.
  const [githubDone, setGithubDone] = useState(false);
  // The same for "Who should do Toolsmith's installs?".
  const [toolsmithDone, setToolsmithDone] = useState(false);

  const load = useCallback(async () => {
    const reply = await getFlow();
    if (!reply.ok || !reply.data?.next) {
      setError(
        reply.status === 0
          ? "No answer from this computer. Is the backend running?"
          : "The wizard could not be loaded.",
      );
      return;
    }
    setError("");
    setFlow(reply.data);
  }, []);

  useEffect(() => {
    void (async () => {
      const state = await getWizardState();
      if (state.ok && state.data?.resume?.offer) setResume(state.data.resume);
      setAsked(true);
      await load();
    })();
  }, [load]);

  let body: React.ReactNode = <p style={HELP}>Loading...</p>;
  if (asked && resume) {
    body = (
      <Resume
        offer={resume}
        onChosen={() => {
          setResume(null);
          void load();
        }}
      />
    );
  } else if (flow) {
    const question = (flow.questions ?? []).find((q) => q.id.endsWith(`:${flow.next}`));
    if (flow.next === "first_look") body = <FirstLookStep onDone={() => void load()} />;
    else if (!githubDone && githubDue(flow))
      body = (
        <GitHubStep
          onDone={() => {
            setGithubDone(true);
            void load();
          }}
        />
      );
    else if (!toolsmithDone && toolsmithDue(flow, githubDone))
      body = (
        <ToolsmithStep
          onDone={() => {
            setToolsmithDone(true);
            void load();
          }}
        />
      );
    else if (question) body = <Question key={question.id} card={question.card} onDone={() => void load()} />;
    else if (flow.next === "find") body = <FindStep onDone={() => void load()} />;
    else if (flow.next === "needs") body = <NeedsStep onDone={() => void load()} />;
    else if (flow.next === "summary") body = <SummaryStep onApproved={() => void load()} />;
    else if (flow.next === "hub" && flow.hub) body = <Question key="hub" card={flow.hub} onDone={() => void load()} />;
    else if (flow.next === "vault") body = <VaultStep flow={flow} onDone={() => void load()} />;
    else if (flow.next === "build" || flow.next === "building") body = <Building flow={flow} onDone={() => void load()} />;
    else if (flow.next === "done" && flow.handoff) body = <Done card={flow.handoff} />;
  }

  return (
    <div className="mx-auto" style={{ maxWidth: 720 }}>
      <ConnectedTo />
      <Card>
        {body}
        <Problem text={error} />
      </Card>
      <ShowAround />
    </div>
  );
}
