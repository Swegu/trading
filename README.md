# Markets

A self-hosted market tracker for personal watchlists, price monitoring, candlestick charts and performance heatmaps.

Market data is provided by Yahoo Finance through yfinance. Quotes may be delayed and availability is not guaranteed. This project is for personal market monitoring, not trade execution or financial advice.

## Docker Compose deployment (no Git required on the server)

Create a folder on your Docker host, save the repository's `docker-compose.yml` and `.env.example` in it, then:

    cp .env.example .env      # set POSTGRES_PASSWORD to a long random secret
    docker compose up -d

Docker pulls the prebuilt Markets image from GitHub Container Registry and the official PostgreSQL image. It does not clone the source repository or build the application on your server.

Open http://YOUR-SERVER-IP:8000. On first launch, the login page guides you through creating the first administrator account. Change the published port with `MARKETS_PORT` in `.env`.

The password is required: Compose refuses to start without it. Keep it unchanged after PostgreSQL has initialised its data volume.

### Persistent data

- PostgreSQL data is stored in the named Docker volume `markets_postgres`.
- The session signing key (and a legacy `app.db`, if you are migrating from the SQLite version) live in `./data` next to the compose file.
- The database is not published to the host; it is accessible only on the private Compose network.
- Back up the `markets_postgres` volume and `./data`.

For updates, use:

    docker compose pull
    docker compose up -d

User management from the command line:

    docker compose exec markets python manage.py list
    docker compose exec markets python manage.py add NAME
    docker compose exec markets python manage.py passwd NAME

Useful commands:

    docker compose logs -f markets
    docker compose logs -f postgres
    docker compose down

`docker compose down` preserves named volumes. Do not use `docker compose down -v` unless you intend to delete the application data and PostgreSQL database.

## Reverse proxy

Point your reverse proxy to the Docker host's LAN address and the published application port.

Example Caddy site:

    markets.example.com {
        reverse_proxy YOUR-SERVER-LAN-IP:8000
    }

Forward the `X-Forwarded-Proto: https` header so secure session cookies are enabled behind HTTPS.

## Features

- Individual user accounts and watchlists
- Market search and quote overview
- Candlestick charts with volume and SMA
- Watchlist heatmaps
- Four selectable visual styles, dark/light mode and density preferences
- Responsive desktop and mobile interface
- PostgreSQL persistence

## Notes

The browser loads Lightweight Charts and D3 from jsDelivr. An internet connection is required for these libraries and Yahoo Finance data. The application uses one Uvicorn worker so in-memory request caching is shared and refresh behaviour remains predictable.
