"use client";

/**
 * One small renderer per field kind, drawn generically from the card.
 *
 * The card says what to draw; this file knows nothing about any particular
 * skill. Adding a kind means teaching both renderers, which is why the list is
 * short (cards.py FIELD_KINDS).
 *
 * SECRETS. `secret_entry` is a password input that is never pre-filled, whose
 * value lives in the caller's component state and nowhere else — not
 * localStorage, not the URL, never logged. The card that asked carries no value
 * for it either; the backend refuses a card that does.
 */

import type { CardField } from "../../_lib/services/wizardService";
import { infoBlock } from "./reviewModel";
import { Field, TextInput, Toggle, inputStyle, microLabel } from "../ui";

export type AnswerValue = string | string[] | boolean | number;

function help(field: CardField) {
  return field.help ? field.help : undefined;
}

/** A run of `info` fields, drawn as one quiet block with at most one small
 *  label. Nothing here is answerable, and nothing is labelled as if it were:
 *  the repeated "SUGGESTED" over every line of a starter catalog was a label
 *  per sentence on a list the owner cannot answer. */
export function InfoBlock({ fields }: { fields: CardField[] }) {
  const block = infoBlock(fields);
  return (
    <div style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}>
      {block.heading ? <div style={microLabel}>{block.heading}</div> : null}
      {block.lines.map((line) => (
        <div
          key={line.id}
          className="flex flex-wrap items-baseline justify-between gap-x-4"
          style={{ marginTop: block.heading || line.label ? 4 : 2 }}
        >
          {line.label ? <span style={microLabel}>{line.label}</span> : null}
          <span
            style={{
              color: "var(--text2)",
              fontSize: 14,
              flex: line.label ? "0 1 auto" : "1 1 100%",
              textAlign: line.label ? "right" : "left",
            }}
          >
            {line.text}
          </span>
        </div>
      ))}
    </div>
  );
}

function ChoiceField({
  field,
  value,
  onChange,
  error,
}: {
  field: CardField;
  value: string;
  onChange: (next: AnswerValue) => void;
  error?: string;
}) {
  const options = field.options || [];
  const typed = field.free_text && !options.some((o) => o.value === value);
  return (
    <Field id={`f-${field.id}`} label={field.label} hint={help(field)} error={error}>
      <select
        id={`f-${field.id}`}
        value={typed ? "" : value}
        onChange={(e) => onChange(e.target.value)}
        style={{ ...inputStyle, appearance: "auto" }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
        {field.free_text ? <option value="">something else</option> : null}
      </select>
      {field.free_text ? (
        <div style={{ marginTop: 8 }}>
          <TextInput
            id={`f-${field.id}-typed`}
            value={typed ? value : ""}
            onChange={(next) => onChange(next)}
            invalid={Boolean(error)}
          />
        </div>
      ) : null}
    </Field>
  );
}

function MultiChoiceField({
  field,
  value,
  onChange,
  error,
}: {
  field: CardField;
  value: string[];
  onChange: (next: AnswerValue) => void;
  error?: string;
}) {
  const chosen = new Set(value);
  return (
    <Field id={`f-${field.id}`} label={field.label} hint={help(field)} error={error}>
      <div>
        {(field.options || []).map((option) => (
          <Toggle
            key={option.value}
            id={`f-${field.id}-${option.value}`}
            checked={chosen.has(option.value)}
            title={option.label}
            onChange={(on) =>
              onChange(
                on
                  ? [...value, option.value]
                  : value.filter((item) => item !== option.value),
              )
            }
          />
        ))}
      </div>
    </Field>
  );
}

function NumberField({
  field,
  value,
  onChange,
  error,
}: {
  field: CardField;
  value: number | null;
  onChange: (next: AnswerValue) => void;
  error?: string;
}) {
  return (
    <Field
      id={`f-${field.id}`}
      label={field.label}
      hint={help(field) || `${field.min} to ${field.max}`}
      error={error}
    >
      <input
        id={`f-${field.id}`}
        type="number"
        min={field.min}
        max={field.max}
        value={value === null || value === undefined ? "" : value}
        aria-invalid={error ? true : undefined}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        style={{ ...inputStyle, borderColor: error ? "var(--puff-red)" : "var(--line)" }}
      />
    </Field>
  );
}

/** Draw one field of whatever kind it is. */
export function FieldRenderer({
  field,
  value,
  onChange,
  error,
}: {
  field: CardField;
  value: AnswerValue | null | undefined;
  onChange: (next: AnswerValue) => void;
  error?: string;
}) {
  switch (field.kind) {
    case "info":
      // A lone info field is a block of one; a run of them is drawn by the
      // caller through InfoBlock.
      return <InfoBlock fields={[field]} />;
    case "choice":
      return (
        <ChoiceField
          field={field}
          value={typeof value === "string" ? value : ""}
          onChange={onChange}
          error={error}
        />
      );
    case "multi_choice":
      return (
        <MultiChoiceField
          field={field}
          value={Array.isArray(value) ? value : []}
          onChange={onChange}
          error={error}
        />
      );
    case "toggle":
      return (
        <div>
          <Toggle
            id={`f-${field.id}`}
            checked={value === true}
            title={field.label}
            description={help(field)}
            onChange={(next) => onChange(next)}
          />
          {error ? (
            <div role="alert" style={{ color: "var(--puff-red)", fontSize: 13, marginTop: 4 }}>
              {error}
            </div>
          ) : null}
        </div>
      );
    case "number":
      return (
        <NumberField
          field={field}
          value={typeof value === "number" ? value : null}
          onChange={onChange}
          error={error}
        />
      );
    case "secret_entry":
      return (
        <Field
          id={`f-${field.id}`}
          label={field.label}
          hint={help(field) || "Held on this screen only, until you apply it."}
          error={error}
        >
          <TextInput
            id={`f-${field.id}`}
            type="password"
            value={typeof value === "string" ? value : ""}
            onChange={(next) => onChange(next)}
            invalid={Boolean(error)}
            autoComplete="new-password"
          />
        </Field>
      );
    default:
      return (
        <Field id={`f-${field.id}`} label={field.label} hint={help(field)} error={error}>
          <TextInput
            id={`f-${field.id}`}
            value={typeof value === "string" ? value : ""}
            onChange={(next) => onChange(next)}
            invalid={Boolean(error)}
          />
        </Field>
      );
  }
}

/** The answers a card starts with: its own proposal, and nothing at all for a
 *  secret. A secret is typed here or it is not sent. */
export function initialAnswers(fields: CardField[]): Record<string, AnswerValue> {
  const out: Record<string, AnswerValue> = {};
  for (const field of fields) {
    if (field.kind === "info") continue;
    if (field.kind === "secret_entry") {
      out[field.id] = "";
      continue;
    }
    const proposed = field.proposed ?? field.current;
    if (proposed === null || proposed === undefined) continue;
    out[field.id] = proposed as AnswerValue;
  }
  return out;
}

/** What is actually sent: an untouched secret is omitted rather than sent as an
 *  empty string, which the card would refuse as an answer nobody gave. */
export function answersToSend(
  fields: CardField[],
  answers: Record<string, AnswerValue>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    if (field.kind === "info") continue;
    const value = answers[field.id];
    if (value === undefined) continue;
    if (field.kind === "secret_entry" && value === "") continue;
    out[field.id] = value;
  }
  return out;
}
