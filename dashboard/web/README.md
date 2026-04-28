# The Firm — Dashboard

Next.js dashboard for the trading intelligence system. Fetches live data from the FastAPI backend or runs in fixture mode.

## Run locally

```bash
npm install
NEXT_PUBLIC_API_URL=http://your-api:8000 npm run dev
```

## Fixture mode (no API required)

```bash
NEXT_PUBLIC_USE_FIXTURES=true NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Tabs

| Tab | Description |
|-----|-------------|
| Overview | Agent status, live activity feed |
| Economics | Kalshi positions, trade history, LLM reasoning |
| Weather | Temperature market performance |
| Sports | Stink-bid paper trades |
| Intelligence | Congressional watchlist, RAG Stock Finder |
| Portfolio | Equity positions, Roth IRA |
| System | Health monitor, eval scores, file viewer |
