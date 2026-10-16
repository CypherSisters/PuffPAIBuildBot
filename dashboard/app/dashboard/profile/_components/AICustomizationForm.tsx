"use client";

/**
 * AI personality, avatar and model settings — converted off the admin template
 * (CardBox / Button / Buttons / Icon+mdi / dark: pairs / #00B4D8 hexes) into the
 * design-match shell. Every endpoint, payload and guard is unchanged; only the
 * markup and the icon set moved.
 */

import { useState, useEffect, useRef, type CSSProperties } from "react";
import MsIcon from "../../../_components/MsIcon";
import { updateAISettings } from "../../../_lib/services/profileService";
import {
  customAvatarUrl,
  deleteCustomAvatar,
  getCustomAvatarStatus,
  uploadCustomAvatar,
  type CustomAvatarStatus,
} from "../../../_lib/services/avatarService";
import type { AISettings } from "../../../_interfaces/api";
import { useAppDispatch } from "../../../_stores/hooks";
import { setAvatarCharacter, setAvatarUrl } from "../../../_stores/avatarSlice";
import {
  AVATAR_CHARACTER_KEY,
  AVATAR_MODEL_URL_KEY,
} from "../../../_components/Avatar/useAvatarCharacter";
import {
  listEngines,
  isAvatarEngineId,
} from "../../../_components/Avatar/engines/registry";

/* ── shell vocabulary ───────────────────────────────────────────────── */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

const microLabel: CSSProperties = {
  fontFamily: JBMONO,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  color: "var(--text3)",
  textTransform: "uppercase",
};

const panelBase: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "24px 28px",
  boxShadow: "var(--shadow)",
};

const fieldStyle: CSSProperties = {
  width: "100%",
  borderRadius: 14,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  fontFamily: NUNITO,
  fontWeight: 600,
  fontSize: 15,
  padding: "11px 14px",
  outline: "none",
};

const readoutStyle: CSSProperties = {
  padding: "14px 16px",
  borderRadius: 18,
  background: "var(--card2)",
  border: "1px solid var(--line)",
};

function PillButton({
  label,
  icon,
  onClick,
  disabled,
  tone = "primary",
}: {
  label: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet" | "danger";
}) {
  const bg =
    tone === "primary"
      ? "linear-gradient(140deg,var(--pink),var(--magenta))"
      : tone === "danger"
        ? `color-mix(in srgb, ${RED} 16%, transparent)`
        : "var(--card2)";
  const fg = tone === "primary" ? "#fff" : tone === "danger" ? RED : "var(--text2)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex cursor-pointer items-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
      style={{
        height: 42,
        padding: "0 20px",
        borderRadius: 99,
        border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
        background: bg,
        color: fg,
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
      }}
    >
      <MsIcon name={icon} size={19} />
      {label}
    </button>
  );
}

function SectionHeading({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div className="mb-5 flex items-center gap-2.5">
      <MsIcon name={icon} size={22} style={{ color: "var(--pink)" }} />
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 24,
          color: "var(--text)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Who greets you on the dashboard. Sourced from the avatar engine registry so
 * new engines appear here automatically. Puff carries the mascot image; the 3D
 * engines show a "3D" badge.
 */
const AVATAR_CHARACTERS = listEngines().map((engine) => ({
  id: engine.id,
  label: engine.label,
  sublabel: engine.description,
  image: engine.id === "puff" ? "/puffpai-logo.png" : null,
  supportsCustomModel: engine.capabilities.customModel === "glb",
}));

const AVATAR_STYLES = [
  { id: "notionists", label: "Notionists", emoji: "R" },
  { id: "bottts", label: "Bottts", emoji: "B" },
  { id: "pixel-art", label: "Pixel Art", emoji: "P" },
  { id: "adventurer", label: "Adventurer", emoji: "A" },
];

/**
 * What "reset" means, split by who stores it.
 *
 * SAVED is exactly what PUT /profile/ai accepts — the endpoint 400s and names
 * any field it does not, rather than accepting a save and dropping the value,
 * so sending a field it has never heard of breaks the whole save. The avatar
 * ENGINE and the owner's own .glb are not settings on that endpoint: the engine
 * is a browser preference and the .glb is a file behind /avatar/custom. They
 * are reset client-side, below.
 */
const DEFAULT_SAVED: Partial<AISettings> = {
  ai_name: "PuffPAI",
  avatar_style: "notionists",
  personality: "helpful",
  temperature: 0.7,
  context_length: 2048,
};

/** The engine to fall back to. Browser-side; see DEFAULT_SAVED. */
const DEFAULT_CHARACTER = "puff";

/** Upload cap shown before the backend's own number lands. The server is the
 *  authority (puffpai.custom_avatar.MAX_UPLOAD_MB); this only exists so the
 *  first paint can state a limit rather than a blank. */
const AVATAR_UPLOAD_MAX_MB = 32;

type Props = {
  settings: AISettings;
  onSaved: (updated: AISettings) => void;
};

export default function AICustomizationForm({ settings, onSaved }: Props) {
  const dispatch = useAppDispatch();
  const [aiName, setAiName] = useState(settings.ai_name || "PuffPAI");
  const [avatarCharacter, setAvatarCharacterState] = useState(
    settings.avatar_character || "puff"
  );
  const [avatarStyle, setAvatarStyle] = useState(settings.avatar_style || "notionists");
  // The owner's own .glb, as the box that holds it describes it. null = not
  // asked yet / backend unreachable, which renders as "no model yet" rather
  // than as a spinner: the drawn Puff is a real answer, not a loading state.
  const [custom, setCustom] = useState<CustomAvatarStatus | null>(null);
  const [temperature, setTemperature] = useState(settings.temperature ?? 0.7);
  const [personality, setPersonality] = useState(settings.personality || "helpful");
  /** The owner's own standing instructions. Stored as `system_prompt` — the name
   *  PUT /profile/ai accepts — and appended to what the bot is told before every
   *  message. "Custom instructions" is what it is called on screen because that
   *  is what it does; "system prompt" is the field name, not the feature. */
  const [instructions, setInstructions] = useState(settings.system_prompt || "");
  const [contextLength, setContextLength] = useState(settings.context_length ?? 2048);

  // Own-model upload. `uploading` covers both the POST and the DELETE so the
  // two buttons can never be pressed against each other.
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const [savingIdentity, setSavingIdentity] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  // Debounce ref for temperature
  const tempTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = (message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Does a custom avatar exist on this box? One read, not polled: the file
  // changes when the owner uploads one, and the handlers below refresh it.
  useEffect(() => {
    let cancelled = false;
    void getCustomAvatarStatus()
      .then((status) => {
        if (!cancelled) setCustom(status);
      })
      .catch(() => {
        // Unreachable: leave null. The copy below says "no model yet", which
        // is what the renderer will actually draw.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Update local state when props change
  useEffect(() => {
    setAiName(settings.ai_name || "PuffPAI");
    setAvatarCharacterState(settings.avatar_character || "puff");
    setAvatarStyle(settings.avatar_style || "notionists");
    setTemperature(settings.temperature ?? 0.7);
    setPersonality(settings.personality || "helpful");
    setInstructions(settings.system_prompt || "");
    setContextLength(settings.context_length ?? 2048);
  }, [settings]);

  // Debounced temperature save
  const handleTemperatureChange = (value: number) => {
    setTemperature(value);
    if (tempTimer.current) clearTimeout(tempTimer.current);
    tempTimer.current = setTimeout(async () => {
      try {
        await updateAISettings({ temperature: value });
        onSaved({ ...settings, temperature: value });
      } catch {
        // Silently fail on debounced saves — user will see state in UI
      }
    }, 600);
  };

  /** Mirror the chosen engine + model URL into the live avatar + next page load. */
  const applyCharacter = (value: string, modelUrl?: string) => {
    if (!isAvatarEngineId(value)) return;
    dispatch(setAvatarCharacter(value));
    try {
      localStorage.setItem(AVATAR_CHARACTER_KEY, value);
    } catch {
      // storage disabled — the profile setting still persists server-side
    }
    // The custom model URL is mirrored unconditionally so store, localStorage
    // and the saved profile always agree with what was just sent. Blank means
    // "use the engine's built-in model", so it must actively CLEAR the stored
    // choice rather than be ignored — otherwise the field can never be un-set.
    const url = (modelUrl ?? "").trim();
    dispatch(setAvatarUrl(url));
    try {
      if (url) {
        localStorage.setItem(AVATAR_MODEL_URL_KEY, url);
      } else {
        localStorage.removeItem(AVATAR_MODEL_URL_KEY);
      }
    } catch {
      // ignore
    }
  };

  /**
   * Upload a .glb from this device. POST /avatar/custom stores it under the
   * appliance's data dir — never in this checkout, which is rebuilt on every
   * deploy — and the browser then points the live avatar at it. There is
   * nothing to save afterwards: the file is the state.
   *
   * The .glb and size checks here are a courtesy (a clearer message, and no
   * 32 MB round trip to learn the answer); the appliance re-checks the real
   * glTF header and its own cap, and is the one that decides.
   */
  const handleModelUpload = async (file: File | null) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".glb")) {
      showToast("Pick a .glb file. Export your avatar as glTF Binary.", "error");
      return;
    }
    setUploading(true);
    try {
      const result = await uploadCustomAvatar(file);
      if (!result.success || !result.avatar) {
        // A rejection is something to read, not a crash. The current avatar is
        // untouched — the server overwrites the slot or it does not.
        showToast(result.error || "That avatar was not accepted.", "error");
        return;
      }
      const url = customAvatarUrl(result.avatar.uploaded_at);
      applyCharacter(avatarCharacter, url);
      setCustom(await getCustomAvatarStatus().catch(() => custom));
      showToast(
        `Model saved (${Math.round(result.avatar.size_bytes / 1024)} KB)`,
        "success"
      );
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Upload failed", "error");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  /** Remove the stored model and go back to the drawn Puff avatar. */
  const handleModelRemove = async () => {
    setUploading(true);
    try {
      await deleteCustomAvatar();
      applyCharacter(avatarCharacter, "");
      setCustom((prev) =>
        prev ? { ...prev, present: false, avatar: null } : prev
      );
      showToast("Model removed", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Remove failed", "error");
    } finally {
      setUploading(false);
    }
  };

  /** The URL the owner's own avatar renders from, or "" when there is none. */
  const modelUrl =
    custom?.present && custom.avatar
      ? customAvatarUrl(custom.avatar.uploaded_at)
      : "";
  /** The cap, stated before the pick rather than after the upload bounces. */
  const maxMb = custom?.max_mb ?? AVATAR_UPLOAD_MAX_MB;
  const modelName = custom?.avatar?.original_name;

  // Save identity (name, avatar, personality)
  const handleSaveIdentity = async () => {
    setSavingIdentity(true);
    try {
      // Only fields PUT /profile/ai accepts. The engine choice and the
      // owner's .glb are NOT among them (see DEFAULT_SAVED): sending either
      // would 400 the whole save, so the engine is applied to the browser and
      // the .glb was already stored by its own endpoint.
      const payload = {
        ai_name: aiName,
        avatar_style: avatarStyle,
        personality,
        // Accepted by the endpoint's _SETTINGS_FIELDS, read back by
        // _ai_settings_view, and used by _system_prompt on every call. Sent
        // trimmed, and an empty string is a real value: it clears them.
        system_prompt: instructions.trim(),
      };
      const res = await updateAISettings(payload);
      if (res.success) {
        applyCharacter(avatarCharacter, modelUrl);
        showToast("AI identity saved", "success");
        onSaved({ ...settings, ...payload });
      } else {
        showToast("Failed to save identity", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save identity", "error");
    } finally {
      setSavingIdentity(false);
    }
  };

  // Save model settings (temperature, context_length)
  const handleSaveSettings = async () => {
    setSavingSettings(true);
    try {
      const payload = {
        temperature,
        context_length: contextLength,
      };
      const res = await updateAISettings(payload);
      if (res.success) {
        showToast("Model settings saved", "success");
        onSaved({ ...settings, ...payload });
      } else {
        showToast("Failed to save settings", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save settings", "error");
    } finally {
      setSavingSettings(false);
    }
  };

  // Reset to defaults
  const handleResetDefaults = async () => {
    setSavingSettings(true);
    try {
      const res = await updateAISettings(DEFAULT_SAVED);
      if (res.success) {
        setAiName(DEFAULT_SAVED.ai_name!);
        setAvatarStyle(DEFAULT_SAVED.avatar_style!);
        setAvatarCharacterState(DEFAULT_CHARACTER);
        // Reset picks the drawn avatar again; it does NOT delete the owner's
        // uploaded .glb. Throwing away a file because somebody pressed
        // "restore defaults" on a temperature slider would be unforgivable —
        // Remove, above, is the only thing that deletes it.
        applyCharacter(DEFAULT_CHARACTER, "");
        setPersonality(DEFAULT_SAVED.personality!);
        setTemperature(DEFAULT_SAVED.temperature!);
        setContextLength(DEFAULT_SAVED.context_length!);
        showToast("Reset to defaults", "success");
        onSaved({ ...settings, ...DEFAULT_SAVED } as AISettings);
      } else {
        showToast("Failed to reset", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to reset", "error");
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <>
      {/* Toast */}
      {toast && (
        <div
          className="fixed right-4 top-4 z-50 flex items-center gap-2"
          style={{
            padding: "14px 20px",
            borderRadius: 99,
            background:
              toast.type === "success"
                ? "linear-gradient(140deg,var(--mint),var(--sky))"
                : `linear-gradient(140deg,${RED},var(--magenta))`,
            color: "#fff",
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
            boxShadow: "var(--shadow)",
          }}
        >
          <MsIcon name={toast.type === "success" ? "check_circle" : "error"} size={20} />
          {toast.message}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        {/* ── AI avatar & identity ──────────────────────────────────── */}
        <div style={panelBase}>
          <SectionHeading icon="smart_toy">Who greets you</SectionHeading>

          {/* Avatar preview */}
          <div className="mb-6 flex flex-col items-center">
            <div
              className="mb-3 flex items-center justify-center overflow-hidden"
              style={{
                width: 104,
                height: 104,
                borderRadius: "50%",
                background: "linear-gradient(140deg,var(--pink),var(--magenta))",
                boxShadow: "0 0 34px color-mix(in srgb, var(--pink) 38%, transparent)",
                color: "#fff",
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 40,
              }}
            >
              {avatarCharacter === "puff" ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src="/puffpai-logo.png"
                  alt="Puff, the PuffPAI cloud mascot"
                  style={{ width: 70, height: 70, objectFit: "contain" }}
                />
              ) : (
                AVATAR_STYLES.find((s) => s.id === avatarStyle)?.emoji || "R"
              )}
            </div>
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 21,
                color: "var(--text)",
              }}
            >
              {aiName}
            </div>
            <div style={microLabel}>Your assistant</div>
          </div>

          {/* AI name */}
          <div className="mb-5">
            <div style={{ ...microLabel, marginBottom: 6 }}>Name</div>
            <input
              type="text"
              value={aiName}
              onChange={(e) => setAiName(e.target.value)}
              style={fieldStyle}
            />
          </div>

          {/* Avatar engine — who actually shows up on the dashboard.
              Sourced from the engine registry, so new engines appear here. */}
          <div className="mb-5">
            <div style={{ ...microLabel, marginBottom: 8 }}>Avatar engine</div>
            <div className="grid grid-cols-3 gap-2.5">
              {AVATAR_CHARACTERS.map((choice) => {
                const on = avatarCharacter === choice.id;
                return (
                  <button
                    key={choice.id}
                    type="button"
                    onClick={() => setAvatarCharacterState(choice.id)}
                    title={choice.sublabel}
                    aria-pressed={on}
                    className="flex cursor-pointer flex-col items-center gap-2"
                    style={{
                      padding: "14px 8px",
                      borderRadius: 20,
                      border: on ? "2px solid var(--pink)" : "1px solid var(--line)",
                      background: on
                        ? "color-mix(in srgb, var(--pink) 12%, transparent)"
                        : "var(--card2)",
                      color: on ? "var(--text)" : "var(--text2)",
                      fontFamily: NUNITO,
                      fontWeight: 700,
                      fontSize: 12,
                    }}
                  >
                    {choice.image ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img
                        src={choice.image}
                        alt={choice.label}
                        style={{ width: 44, height: 44, objectFit: "contain" }}
                      />
                    ) : (
                      <span
                        className="flex items-center justify-center"
                        style={{
                          width: 44,
                          height: 44,
                          borderRadius: "50%",
                          background:
                            "linear-gradient(140deg, color-mix(in srgb, var(--sky) 40%, transparent), color-mix(in srgb, var(--lav) 40%, transparent))",
                          fontFamily: JBMONO,
                          fontWeight: 700,
                          fontSize: 14,
                          color: "var(--text)",
                        }}
                      >
                        3D
                      </span>
                    )}
                    <span className="text-center leading-tight">{choice.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Your own 3D model — only for engines that load a GLB.
                No model ships with this build, so this is the route to a 3D
                face. The file goes to the appliance's data dir, not into this
                checkout, and survives restarts and redeploys. */}
            {AVATAR_CHARACTERS.find((c) => c.id === avatarCharacter)
              ?.supportsCustomModel && (
              <div
                className="mt-3.5"
                style={{
                  padding: 18,
                  borderRadius: 20,
                  border: "1px solid var(--line)",
                  background: "var(--card2)",
                }}
              >
                <div className="mb-2 flex items-center gap-2">
                  <MsIcon name="deployed_code" size={19} style={{ color: "var(--sky)" }} />
                  <span style={microLabel}>Your own 3D model (.glb)</span>
                </div>

                <p
                  style={{
                    margin: "0 0 12px",
                    fontFamily: NUNITO,
                    fontWeight: 500,
                    fontSize: 14,
                    lineHeight: 1.5,
                    color: "var(--text2)",
                  }}
                >
                  No model ships with PuffPAI. None of the ones we had were
                  ours to redistribute, so bringing your own is the supported
                  path and the only one. Export one from your own
                  Ready&nbsp;Player&nbsp;Me / Avaturn account, or from Blender
                  as <em>glTF Binary (.glb)</em>, up to {maxMb} MB. It is
                  stored on your appliance, under its data dir rather than in
                  this checkout, and never sent anywhere.{" "}
                  <a
                    href="https://github.com/CypherSisters/PuffPAIBuildBot/blob/main/docs/AVATARS.md"
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: "var(--pink)", textDecoration: "underline" }}
                  >
                    What a model needs
                  </a>
                </p>

                <input
                  ref={fileInput}
                  type="file"
                  accept=".glb,model/gltf-binary"
                  className="hidden"
                  onChange={(e) => handleModelUpload(e.target.files?.[0] ?? null)}
                />

                <div className="flex flex-wrap items-center gap-2.5">
                  <PillButton
                    icon="upload"
                    label={modelUrl ? "Replace model" : "Upload .glb"}
                    onClick={() => fileInput.current?.click()}
                    disabled={uploading}
                  />
                  {modelUrl && (
                    <PillButton
                      icon="delete"
                      label="Remove"
                      tone="danger"
                      onClick={handleModelRemove}
                      disabled={uploading}
                    />
                  )}
                  <span style={{ ...microLabel, textTransform: "none", letterSpacing: 0 }}>
                    {modelUrl
                      ? `In use: ${modelName || "your own model"}.`
                      : "No model yet. Puff is drawn instead."}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Avatar style */}
          <div className="mb-5">
            <div style={{ ...microLabel, marginBottom: 8 }}>Avatar style</div>
            <div className="grid grid-cols-2 gap-2.5">
              {AVATAR_STYLES.map((style) => {
                const on = avatarStyle === style.id;
                return (
                  <button
                    key={style.id}
                    type="button"
                    onClick={() => setAvatarStyle(style.id)}
                    aria-pressed={on}
                    className="flex cursor-pointer items-center gap-2.5"
                    style={{
                      padding: "10px 12px",
                      borderRadius: 18,
                      border: on ? "2px solid var(--pink)" : "1px solid var(--line)",
                      background: on
                        ? "color-mix(in srgb, var(--pink) 12%, transparent)"
                        : "var(--card2)",
                      color: on ? "var(--text)" : "var(--text2)",
                      fontFamily: NUNITO,
                      fontWeight: 700,
                      fontSize: 14,
                    }}
                  >
                    <span
                      className="flex shrink-0 items-center justify-center"
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: "50%",
                        background:
                          "linear-gradient(140deg, color-mix(in srgb, var(--sky) 40%, transparent), color-mix(in srgb, var(--lav) 40%, transparent))",
                        fontFamily: JBMONO,
                        fontWeight: 700,
                        fontSize: 13,
                        color: "var(--text)",
                      }}
                    >
                      {style.emoji}
                    </span>
                    {style.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Personality and the owner's own instructions.
              Both go into the system prompt of every model call
              (api_server._system_prompt reads personality and system_prompt out
              of ai_settings.json on each request), so the honest claim is "the
              next message", not "immediately" and not "after a restart". Saying
              exactly that is the point: a knob whose effect nobody can describe
              is a knob nobody trusts. */}
          <div className="mb-6">
            <div style={{ ...microLabel, marginBottom: 6 }}>Personality</div>
            <select
              value={personality}
              onChange={(e) => setPersonality(e.target.value)}
              style={fieldStyle}
            >
              <option value="helpful">Helpful &amp; Friendly</option>
              <option value="concise">Concise &amp; Direct</option>
              <option value="creative">Creative &amp; Playful</option>
              <option value="technical">Technical &amp; Precise</option>
            </select>
            <p
              className="mt-1.5"
              style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text3)" }}
            >
              Changes how the bot talks. Takes effect on the next message.
            </p>
          </div>

          {/* Custom instructions */}
          <div className="mb-6">
            <div style={{ ...microLabel, marginBottom: 6 }}>Custom instructions</div>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="e.g. Answer in metric. Never suggest ordering anything."
              style={{ ...fieldStyle, resize: "vertical" }}
            />
            <p
              className="mt-1.5"
              style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text3)" }}
            >
              Added to what the bot is told before every message, so it applies
              from the next one on. It shapes how the bot answers. It does not
              grant it anything: what it can reach is decided by the build, not
              by this box.
            </p>
          </div>

          <PillButton
            icon="save"
            label={savingIdentity ? "Saving…" : "Save identity"}
            onClick={handleSaveIdentity}
            disabled={savingIdentity}
          />
        </div>

        {/* ── Model settings ────────────────────────────────────────── */}
        <div style={panelBase}>
          <SectionHeading icon="tune">Model settings</SectionHeading>

          {/* Temperature */}
          <div className="mb-6">
            <div className="mb-2.5 flex items-center justify-between gap-3">
              <span style={microLabel}>Temperature</span>
              <span
                style={{
                  padding: "4px 12px",
                  borderRadius: 99,
                  background: "color-mix(in srgb, var(--pink) 16%, transparent)",
                  color: "var(--pink)",
                  fontFamily: JBMONO,
                  fontWeight: 700,
                  fontSize: 13,
                }}
              >
                {temperature.toFixed(1)}
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={temperature}
              onChange={(e) => handleTemperatureChange(parseFloat(e.target.value))}
              className="w-full"
              style={{ accentColor: "var(--pink)" }}
            />
            <div
              className="mt-1.5 flex justify-between"
              style={{ ...microLabel, fontSize: 10 }}
            >
              <span>Precise</span>
              <span>Creative</span>
            </div>
          </div>

          {/* Context length */}
          <div className="mb-6">
            <div style={{ ...microLabel, marginBottom: 6 }}>Context length</div>
            <select
              value={contextLength}
              onChange={(e) => setContextLength(parseInt(e.target.value))}
              style={fieldStyle}
            >
              <option value={1024}>1024 tokens (Fast)</option>
              <option value={2048}>2048 tokens (Balanced)</option>
              <option value={4096}>4096 tokens (Extended)</option>
              <option value={8192}>8192 tokens (Maximum)</option>
            </select>
          </div>

          {/* LLM backend — from the API */}
          <div className="mb-5">
            <div style={{ ...microLabel, marginBottom: 6 }}>Brain</div>
            <div style={readoutStyle}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span
                  style={{
                    fontFamily: NUNITO,
                    fontWeight: 700,
                    fontSize: 16,
                    color: "var(--text)",
                  }}
                >
                  {settings.model || "No model reported"}
                </span>
                <span
                  style={{
                    padding: "4px 12px",
                    borderRadius: 99,
                    background: settings.backend
                      ? "color-mix(in srgb, var(--mint) 16%, transparent)"
                      : "var(--chip)",
                    color: settings.backend ? "var(--mint)" : "var(--text3)",
                    fontFamily: JBMONO,
                    fontWeight: 700,
                    fontSize: 11,
                    letterSpacing: ".08em",
                    textTransform: "uppercase",
                  }}
                >
                  {settings.backend || "backend unknown"}
                </span>
              </div>
            </div>
          </div>

          {/* Embedding model — from the API */}
          <div className="mb-6">
            <div style={{ ...microLabel, marginBottom: 6 }}>Embeddings</div>
            <div style={readoutStyle}>
              <span
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 700,
                  fontSize: 16,
                  color: "var(--text)",
                }}
              >
                {settings.embedding_model || "No embedding model reported"}
              </span>
              <div style={{ ...microLabel, marginTop: 4, fontSize: 10 }}>
                Used to retrieve from your own documents
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <PillButton
              icon="save"
              label={savingSettings ? "Saving…" : "Save settings"}
              onClick={handleSaveSettings}
              disabled={savingSettings}
            />
            <PillButton
              icon="restart_alt"
              label="Reset to defaults"
              tone="quiet"
              onClick={handleResetDefaults}
              disabled={savingSettings}
            />
          </div>
        </div>
      </div>
    </>
  );
}
