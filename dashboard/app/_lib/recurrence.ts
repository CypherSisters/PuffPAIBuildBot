/**
 * Calendar event recurrence expansion.
 *
 * The backend stores events with a single `start` time and an optional
 * `rec` field describing how to repeat them. This module expands those
 * stored events into concrete instances for a date range so the UI can
 * display every occurrence.
 *
 * Supported `rec` values:
 *   - "daily"          — every day
 *   - "weekdays"       — Mon–Fri
 *   - "weekly"         — same day of week each week
 *   - "biweekly"       — every 14 days
 *   - "monthly"        — same day of month each month
 *   - "yearly"         — same date each year
 *   - object form: { freq, interval?, byday?, count?, until? }
 *     where byday is a list like ["MO","TU","WE","TH","FR"]
 *
 * Returns clones of the original event with `start`/`end` overridden
 * for each occurrence and a synthesized `_occurrenceKey` for React keys.
 */

export interface CalendarEvent {
  id: string;
  title: string;
  start: string; // ISO datetime
  end?: string;
  cat?: string;
  to?: string;
  prio?: string;
  rec?: string | RecurrenceRule | null;
  loc?: string | null;
  src?: string;
  taskId?: string;
  [key: string]: any;
}

export interface RecurrenceRule {
  freq: "daily" | "weekly" | "monthly" | "yearly";
  interval?: number;
  byday?: string[]; // ["MO", "TU", ...]
  count?: number;
  until?: string; // ISO date
}

const DAY_CODES = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];

function parseISO(s: string): Date | null {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

function addMonths(d: Date, n: number): Date {
  const r = new Date(d);
  r.setMonth(r.getMonth() + n);
  return r;
}

function addYears(d: Date, n: number): Date {
  const r = new Date(d);
  r.setFullYear(r.getFullYear() + n);
  return r;
}

function normalizeRule(rec: string | RecurrenceRule | null | undefined): RecurrenceRule | null {
  if (!rec) return null;
  if (typeof rec === "object") return rec;
  const lower = rec.toLowerCase().trim();
  switch (lower) {
    case "daily":
    case "every day":
      return { freq: "daily", interval: 1 };
    case "weekdays":
    case "every weekday":
    case "mon-fri":
    case "weekday":
      return { freq: "weekly", interval: 1, byday: ["MO", "TU", "WE", "TH", "FR"] };
    case "weekly":
      return { freq: "weekly", interval: 1 };
    case "biweekly":
    case "every two weeks":
      return { freq: "weekly", interval: 2 };
    case "monthly":
      return { freq: "monthly", interval: 1 };
    case "yearly":
    case "annually":
      return { freq: "yearly", interval: 1 };
    case "none":
    case "":
      return null;
    default:
      return null;
  }
}

/**
 * Expand a single event (which may or may not be recurring) into concrete
 * occurrences within [rangeStart, rangeEnd]. Returns at most `maxOccurrences`
 * to prevent runaway expansion.
 */
export function expandEvent(
  event: CalendarEvent,
  rangeStart: Date,
  rangeEnd: Date,
  maxOccurrences = 366
): CalendarEvent[] {
  const start = parseISO(event.start);
  if (!start) return [];

  const rule = normalizeRule(event.rec);

  // Non-recurring event — return as-is if it falls in range
  if (!rule) {
    if (start >= rangeStart && start <= rangeEnd) {
      return [{ ...event, _occurrenceKey: `${event.id}-${start.toISOString()}` }];
    }
    return [];
  }

  const interval = rule.interval || 1;
  const occurrences: CalendarEvent[] = [];
  const until = rule.until ? parseISO(rule.until) : null;

  // Compute event duration so we can preserve end time on each occurrence
  const end = parseISO(event.end || event.start) || start;
  const durationMs = end.getTime() - start.getTime();

  let cursor = new Date(start);
  let count = 0;

  // Don't iterate forever — bound by maxOccurrences AND rangeEnd AND `until`/`count`
  while (cursor <= rangeEnd && occurrences.length < maxOccurrences) {
    if (rule.count && count >= rule.count) break;
    if (until && cursor > until) break;

    // Check if this occurrence is in our display range
    if (cursor >= rangeStart) {
      // For weekly with byday, only emit on listed days
      if (rule.freq === "weekly" && rule.byday && rule.byday.length > 0) {
        const dayCode = DAY_CODES[cursor.getDay()];
        if (rule.byday.includes(dayCode)) {
          const occEnd = new Date(cursor.getTime() + durationMs);
          occurrences.push({
            ...event,
            start: cursor.toISOString(),
            end: occEnd.toISOString(),
            _occurrenceKey: `${event.id}-${cursor.toISOString()}`,
            _isRecurringInstance: true,
          });
          count++;
        }
      } else {
        const occEnd = new Date(cursor.getTime() + durationMs);
        occurrences.push({
          ...event,
          start: cursor.toISOString(),
          end: occEnd.toISOString(),
          _occurrenceKey: `${event.id}-${cursor.toISOString()}`,
          _isRecurringInstance: count > 0,
        });
        count++;
      }
    }

    // Step cursor forward
    if (rule.freq === "weekly" && rule.byday && rule.byday.length > 0) {
      // For weekday rules, advance by 1 day at a time
      cursor = addDays(cursor, 1);
    } else if (rule.freq === "daily") {
      cursor = addDays(cursor, interval);
    } else if (rule.freq === "weekly") {
      cursor = addDays(cursor, 7 * interval);
    } else if (rule.freq === "monthly") {
      cursor = addMonths(cursor, interval);
    } else if (rule.freq === "yearly") {
      cursor = addYears(cursor, interval);
    } else {
      break;
    }
  }

  return occurrences;
}

/**
 * Expand a list of events into all occurrences within a date range.
 */
export function expandEvents(
  events: CalendarEvent[],
  rangeStart: Date,
  rangeEnd: Date
): CalendarEvent[] {
  const out: CalendarEvent[] = [];
  for (const event of events || []) {
    out.push(...expandEvent(event, rangeStart, rangeEnd));
  }
  out.sort((a, b) => a.start.localeCompare(b.start));
  return out;
}

