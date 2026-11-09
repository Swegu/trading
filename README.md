# Markets

A self-hosted market tracker for personal watchlists, price monitoring, candlestick charts and performance heatmaps.

Market data is provided by Yahoo Finance through yfinance. Quotes may be delayed and availability is not guaranteed. This project is for personal market monitoring, not trade execution or financial advice.

## Start with Docker

For a fresh installation, clone the feature branch and start the stack:

    git clone -b feature/market-tracker-redesign-postgres https://github.com/Swegu/trading.git
    cd trading
    docker compose up -d --build

For an existing checkout, switch to the feature branch once, then future updates are just pull and start:

    git fetch origin
    git checkout feature/market-tracker-redesign-postgres
    git pull
    docker compose up -d --build

Open http://YOUR-SERVER-IP:8000. On first launch, the login page guides you through creating the first administrator account. No separate database installation, SQL commands, migrations or setup scripts are required.

The default published port is 8000. Change it by setting MARKETS_PORT in a .env file.

## PostgreSQL and data

Docker Compose starts both the Markets application and PostgreSQL. PostgreSQL is not published to the host; it is accessible only on the private Compose network.

- PostgreSQL data is stored in the named Docker volume markets_postgres.
- Application files and the session signing key are stored in ./data.
- Existing installations with ./data/app.db are automatically imported into PostgreSQL on first startup. Users, password hashes, admin status, watchlists and settings are preserved.
- The original SQLite file is left in place as a backup. Do not delete ./data until you have verified the migrated installation.
- Back up both the markets_postgres Docker volume and the ./data directory.

For a custom database password, create .env before the first start using .env.example as a template. Use a long alphanumeric or hexadecimal value. The database password must remain the same after the PostgreSQL volume has been initialized.

Useful commands:

    docker compose logs -f markets
    docker compose logs -f postgres
    docker compose down

docker compose down preserves the database volume. Do not use docker compose down -v unless you intend to delete the PostgreSQL database.

## Reverse proxy

The application listens on port 8000 by default. Point your reverse proxy to the host LAN address and this port.

Example Caddy site:

    markets.example.com {
        reverse_proxy YOUR-SERVER-LAN-IP:8000
    }

Forward the X-Forwarded-Proto: https header so secure session cookies are enabled behind HTTPS.

## Features

- Individual user accounts and watchlists
- Market search and quote overview
- Candlestick charts with volume and SMA
- Watchlist heatmaps
- Four selectable visual styles, dark/light mode and density preferences
- Responsive desktop and mobile interface
- PostgreSQL persistence with automatic SQLite user-data migration

Shell administration fallback (run inside the app container):

    docker exec -it markets python manage.py list
    docker exec -it markets python manage.py add USERNAME
    docker exec -it markets python manage.py passwd USERNAME
    docker exec -it markets python manage.py delete USERNAME

## Notes

The browser loads Lightweight Charts and D3 from jsDelivr. An internet connection is required for these libraries and Yahoo Finance data. The application uses one Uvicorn worker so in-memory request caching is shared and refresh behaviour remains predictable.
