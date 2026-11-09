# Markets

A self-hosted market tracker for personal watchlists, price monitoring, candlestick charts and performance heatmaps.

Market data is provided by Yahoo Finance through yfinance. Quotes may be delayed and availability is not guaranteed. This project is for personal market monitoring, not trade execution or financial advice.

## Docker Compose deployment (no Git required on the server)

Create a folder on your Docker host, save the repository's \`docker-compose.yml\` in that folder, and start it:

    docker compose up -d

Docker pulls the prebuilt Markets image from GitHub Container Registry and the official PostgreSQL image. It does not clone the source repository or build the application on your server.

Open http://YOUR-SERVER-IP:8000. On first launch, the login page guides you through creating the first administrator account.

The default published port is 8000. Change it in the Compose file by changing \`8000:8000\` to your preferred host port.

### First-run configuration

Before the first start, replace \`replace-this-with-a-long-random-password\` in both places in the Compose file with the same long random password. Keep the value unchanged after PostgreSQL has initialized its data volume.

### Persistent data

- PostgreSQL data is stored in the named Docker volume \`markets_postgres\`.
- Application data and the session signing key are stored in the named Docker volume \`markets_data\`.
- The database is not published to the host; it is accessible only on the private Compose network.
- Back up both named volumes.

For updates, use:

    docker compose pull
    docker compose up -d

Useful commands:

    docker compose logs -f markets
    docker compose logs -f postgres
    docker compose down

\`docker compose down\` preserves named volumes. Do not use \`docker compose down -v\` unless you intend to delete the application data and PostgreSQL database.

## Reverse proxy

Point your reverse proxy to the Docker host's LAN address and the published application port.

Example Caddy site:

    markets.example.com {
        reverse_proxy YOUR-SERVER-LAN-IP:8000
    }

Forward the \`X-Forwarded-Proto: https\` header so secure session cookies are enabled behind HTTPS.

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
