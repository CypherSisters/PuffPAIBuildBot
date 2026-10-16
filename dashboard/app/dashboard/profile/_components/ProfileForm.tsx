"use client";

/**
 * Name + e-mail, saved to PUT /profile. Same data contract as before; the
 * Formik/CardBox/FormField scaffolding is gone in favour of two controlled
 * inputs in the shell's vocabulary.
 *
 * The "Avatar — Upload / Max 500kb" card that used to sit above this form is
 * removed. It was a template `FormFilePicker` with no change handler and no
 * endpoint behind it: picking a file did nothing, and there is no avatar IMAGE
 * upload in this build at all. The real avatar control is the .glb model
 * uploader in AICustomizationForm, which posts to /avatar/custom.
 */

import { useEffect, useState, type CSSProperties } from "react";
import MsIcon from "../../../_components/MsIcon";
import { updateProfile } from "../../../_lib/services/profileService";
import type { UserProfile } from "../../../_interfaces/api";

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

type Props = {
  profile: UserProfile;
  onSaved: (updated: UserProfile) => void;
};

export default function ProfileForm({ profile, onSaved }: Props) {
  const [name, setName] = useState(profile?.name || "");
  const [email, setEmail] = useState(profile?.email || "");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(
    null
  );

  // Mirror late-arriving props, the way the old Formik `enableReinitialize` did.
  useEffect(() => {
    setName(profile?.name || "");
    setEmail(profile?.email || "");
  }, [profile]);

  const showToast = (message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const handleSubmit = async () => {
    setSaving(true);
    try {
      const res = await updateProfile({ name, email });
      if (res.success) {
        showToast("Profile saved", "success");
        onSaved({ name, email });
      } else {
        showToast("Failed to save profile", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save profile", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={panelBase} className="flex flex-col">
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 24,
          color: "var(--text)",
          marginBottom: 18,
        }}
      >
        Who you are
      </div>

      {toast && (
        <div
          className="mb-4 flex items-center gap-2"
          style={{
            padding: "12px 16px",
            borderRadius: 16,
            border: `1px solid color-mix(in srgb, ${
              toast.type === "success" ? "var(--mint)" : RED
            } 40%, transparent)`,
            background: `color-mix(in srgb, ${
              toast.type === "success" ? "var(--mint)" : RED
            } 12%, transparent)`,
            color: toast.type === "success" ? "var(--mint)" : RED,
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          <MsIcon name={toast.type === "success" ? "check_circle" : "error"} size={19} />
          {toast.message}
        </div>
      )}

      <div className="flex flex-col gap-4">
        <div>
          <div style={{ ...microLabel, marginBottom: 6 }}>Name</div>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="What Puff should call you"
            style={fieldStyle}
          />
        </div>
        <div>
          <div style={{ ...microLabel, marginBottom: 6 }}>E-mail</div>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder="Optional, stays on this computer"
            style={fieldStyle}
          />
        </div>
      </div>

      <div className="mt-6">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={saving}
          className="inline-flex cursor-pointer items-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
          style={{
            height: 44,
            padding: "0 22px",
            borderRadius: 99,
            border: "1px solid transparent",
            background: "linear-gradient(140deg,var(--pink),var(--magenta))",
            color: "#fff",
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          <MsIcon name="save" size={19} />
          {saving ? "Saving…" : "Save profile"}
        </button>
      </div>
    </div>
  );
}
