# PuffPAI Dashboard

The web dashboard for [PuffPAI](../README.md), a Next.js (App Router) UI for
chat, your knowledge base, uploaded documents, the encrypted vault, smart-home
control, media, workflows, and the agent team. It talks to the PuffPAI backend
(`puffpai serve`) through a same-origin server-side proxy, so the API key never
reaches the browser.

## Quick start

Requires **Node 20+** (`^20.19 || >=22.12`) and a running backend.

```bash
# 1. In the repo root, start the backend (prints where it stored the API key):
puffpai serve

# 2. In this directory, configure and build the dashboard:
cd dashboard
cp .env.example .env.local        # then paste your backend API key into it
npm install
npm run build
npm start                         # serves on http://localhost:3003
```

See [`.env.example`](.env.example) for every setting, and the root
[README](../README.md#dashboard) and
[docs/GETTING_STARTED.md](../docs/GETTING_STARTED.md) for the full walkthrough.

## A note on the login page

The login page is **cosmetic**. It does not authenticate anyone. Real access
control is the backend API key carried by the server-side proxy. Run the
dashboard only on a machine and network you trust (bind it to localhost / a
trusted LAN; do not expose it to the public internet).

## Scripts

| Script           | What it does                            |
| ---------------- | --------------------------------------- |
| `npm run dev`    | Dev server with Turbopack on port 3003  |
| `npm run build`  | Production build                        |
| `npm start`      | Serve the production build on port 3003 |
| `npm run lint`   | ESLint                                  |
| `npm run format` | Prettier                                |

## Attribution

The UI is built on the MIT-licensed
[Admin One](https://justboil.me/tailwind-admin-templates/free-react-dashboard/)
React/Tailwind template by JustBoil.me. See [LICENSE](LICENSE).
