# The Firm — Dashboard

A 7-tab Next.js dashboard for The Firm trading system.

**Live demo:** see parent repo README for Vercel URL.

## Tabs

| Tab | What it shows |
|-----|--------------|
| Overview | Agent status grid, live activity feed |
| Economics | Open Kalshi positions, trade history, LLM reasoning |
| Weather | Temperature market performance, 19 cities |
| Sports | Stink-bid paper trades by strategy |
| Intelligence | Congressional watchlist, RAG Stock Finder |
| Portfolio | Live equity positions, Roth IRA |
| System | Service health, file viewer, performance analytics |

## Run locally

```bash
npm install

# Against live API
NEXT_PUBLIC_API_URL=http://your-api:8000 npm run dev

# With fixture data (no API required)
NEXT_PUBLIC_USE_FIXTURES=true npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Fixture mode

`public/data/*.json` contains real snapshots from the live system. Set
`NEXT_PUBLIC_USE_FIXTURES=true` to serve these instead of calling the API.
The **Stock Finder** (Intelligence tab) always calls the live API regardless
of fixture mode — it's the only feature making real-time LLM calls.
