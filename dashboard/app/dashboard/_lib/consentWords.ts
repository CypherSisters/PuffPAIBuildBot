// The consent line and the outside services that are on, as the sidebar card
// and the Developer pages show them. Every word and every count comes from
// the backend (/system/privacy "consent", built by
// privacy_explainer.consent_summary from the rows of "What leaves your
// computer"); nothing here invents a count or a promise.
// Free of path aliases so it stays plain to read and test.

export interface ConsentSummary {
  consent_line?: string;
  count?: number | null;
  on?: string[];
  line?: string;
}

/** The privacy reply's fields this file reads. */
export interface PrivacyConsent {
  outside_services_on?: number | null;
  consent?: ConsentSummary | null;
}

/** How many outside services are on, or null when the backend could not
 *  read its switches (never shown as 0). */
export function onCount(p: PrivacyConsent | null | undefined): number | null {
  const c = p?.consent?.count;
  if (typeof c === "number") return c;
  const n = p?.outside_services_on;
  return typeof n === "number" ? n : null;
}

/** The names of the services that are on, in the order "What leaves your
 *  computer" lists them. */
export function onNames(p: PrivacyConsent | null | undefined): string[] {
  const names = p?.consent?.on;
  return Array.isArray(names) ? names.filter((n) => typeof n === "string" && n.trim() !== "") : [];
}

/** "N outside services on", "No outside services on", or "not checked". */
export function countWords(p: PrivacyConsent | null | undefined): string {
  const on = onCount(p);
  if (on === null) return "Outside services not checked";
  if (on === 0) return "No outside services on";
  return `${on} outside ${on === 1 ? "service" : "services"} on`;
}

/** The backend's consent line, or empty when it did not send one. */
export function consentLine(p: PrivacyConsent | null | undefined): string {
  return (p?.consent?.consent_line ?? "").trim();
}
