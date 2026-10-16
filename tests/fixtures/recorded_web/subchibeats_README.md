# Bunny's Big Day &middot; Wedding Planning Dashboard

A polished, fully-featured **wedding planning dashboard** in a single self-contained
`index.html` file. No build tools, no frameworks, no CDNs, no backend — just open it
in a browser. All data is saved privately in the browser via `localStorage`.

**Live demo:** https://subchibeats.github.io/wedding-planner/

## Features

Eight tabs, each a real planning tool:

| Tab | What it does |
| --- | --- |
| **Overview** | Countdown, RSVP / budget / checklist progress bars, live stat cards, smart warnings |
| **Guest List** | Add/edit/delete guests, search + filter + sort, auto RSVP counts and meal breakdown |
| **Seating Chart** | Visual drag-to-arrange floor-plan builder — tables in any shape (round, oval, square, rectangle, head), adjustable size & seats, chairs drawn around each table with guest names, dance-floor / stage / bar / gift elements, zoom, over-capacity & unseated warnings, plus a tap-friendly **List** view |
| **Budget** | Itemized tracker, `Balance = Actual − Deposit`, projected vs. total, over-budget / over-estimate / payment-due alerts |
| **Day-Of Timeline** | Run-sheet anchored to the ceremony-end time, re-flow from anchor, complete toggles, vendor view |
| **Vendors** | Side-by-side comparison matrix (up to 3 per category) + locked-in contact directory |
| **Tasks / Checklist** | Grouped by planning phase, progress %, overdue warnings, completed styling |
| **Export / Print** | CSV export (guests, budget, vendors, timeline), full JSON backup + import, copy summary, print view |

## Highlights

- **Everything is computed** — guest totals, meal counts, budget balances and checklist
  progress are all derived from your data in JavaScript, never hardcoded.
- **Persists locally** — refresh-safe via `localStorage`; back up / restore with JSON.
- **Editable setup** — wedding date, guest goal, budget, vibe, location and ceremony-end
  time are all editable and saved.
- **Confirmation prompts** before any destructive action (delete / reset / import).
- **Responsive & accessible** — mobile-first, keyboard-friendly, labelled forms, good contrast.
- **Elegant aesthetic** — soft blush / sage / champagne palette, rounded cards, serif display type.

## Use it

Open `index.html` in any modern browser. The app starts pre-loaded with realistic demo
data so you can explore it immediately — use **Reset Demo Data** to restore it, or clear
everything and enter your own. Your data never leaves your device.

## Tech

Plain semantic HTML, embedded CSS and vanilla JavaScript. Single file, zero dependencies.

## License

[MIT](LICENSE) © Sahib Singh
