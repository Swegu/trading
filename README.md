# Markets

Self-hosted market dashboard: watchlists, candlestick charts, heatmaps, search, per-user logins.
Data comes from Yahoo Finance via `yfinance` (unofficial, personal use only, delayed quotes).

## Run

```bash
cp .env.example .env        # set PROXY_NETWORK to your reverse proxy's Docker network
docker compose up -d --build
docker exec -it markets python manage.py add YOUR_NAME   # first user becomes admin
```

Caddy:

```
trade.example.org {
    reverse_proxy markets:8000
}
```

## Users

The admin adds and removes users in Settings. Shell fallback (run with `docker exec -it markets`):
`python manage.py list | add NAME | passwd NAME | delete NAME`

## Data

Everything lives in `./data` (SQLite database, session key). It is git-ignored; back it up separately.
Delete `data/secret.key` and restart to sign everyone out.
`app/config.json` is the starter watchlist for new users.

## Notes

- Without Docker: `pip install -r requirements.txt && cd app && DATA_DIR=../data uvicorn main:app`
- The page loads Lightweight Charts (TradingView, Apache-2.0, attribution required if public) and d3 from jsDelivr.
