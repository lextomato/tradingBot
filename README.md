# Grid Trading Bot  🚀

Automated grid‑trading strategy for **ETH/USDT** (Binance Spot) with a real‑time
Streamlit dashboard. Runs inside Docker, orchestrated by Traefik for zero‑downtime
HTTPS deployments.

---

## ✨ Main Features

| Module                    | Highlights                                                                                                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`grid_trading_bot.py`** | \* Dynamic grid range: `±spread_usd` around current price.<br>\* Auto‑compute `grid_size` to hit a target gain per fill.<br>\* Trailing‑stop & global stop‑loss.<br>\* SQLite + CSV trade journal.<br>\* Commission‑aware PnL calculation. |
| **`dashboard.py`**        | \* Live metrics via Streamlit.<br>\* Autorefresh every <code>REFRESH_EVERY</code> seconds.<br>\* Exposure widget (ETH/USDT).<br>\* Cumulative PnL chart & trades table.                                                                    |

---

## ⚙️ Requirements

- Python 3.10+
- Binance API key (spot)
- Docker & Docker Compose (for production)

```bash
# Ubuntu 22.04
sudo apt-get install docker-ce docker-compose-plugin -y
```

---

## 🔑 Environment Variables

| Var          | Description              |
| ------------ | ------------------------ |
| `API_KEY`    | Binance API key          |
| `SECRET_KEY` | Binance secret key       |
| `TESTNET`    | `True` = Binance Testnet |

Create a local **`.env`** (never commit it!):

```dotenv
API_KEY=your_key
SECRET_KEY=your_secret
TESTNET=True
```

---

## 🧑‍💻 Local Development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python grid_trading_bot.py          # start bot
streamlit run dashboard.py          # open http://localhost:8501
```

---

## 🐳 Docker Quick Start

```bash
# build image & start both services
export $(grep -v "^#" .env | xargs)   # load env vars for build‑arg

docker compose up -d --build
```

**docker‑compose.yml** (excerpt):

```yaml
services:
  bot:
    build: .
    command: python grid_trading_bot.py
    env_file: /srv/secret-envs/gridbot.env
    volumes:
      - data:/app

  dashboard:
    image: gridbot:latest
    command: >
      streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0
    labels:
      - traefik.enable=true
      - traefik.http.routers.gridbot.rule=Host(`gridbot.example.com`)
      - traefik.http.routers.gridbot.entrypoints=websecure
      - traefik.http.routers.gridbot.tls.certresolver=le
    volumes:
      - data:/app
volumes:
  data:
networks:
  web:
    external: true
```

---

## 🚀 Production Deploy (VPS + Traefik)

1. Clone repo to **`/srv/gridbot`** on the server.
2. Copy secrets to **`/srv/secret-envs/gridbot.env`** (`chmod 600`).
3. `docker compose up -d --build`.
4. Set DNS ➡ `gridbot.example.com` → your VPS IP.
5. Traefik issues Let’s Encrypt certs automatically.

> **Tip:** keep Watchtower running to auto‑pull updates.

---

## 🔄 CI/CD via GitHub Actions

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: ghcr.io/<user>/gridbot:latest
      - uses: appleboy/scp-action@v0.1.4
        with:
          host: ${{ secrets.VPS_IP }}
          username: deploy
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /srv/gridbot
            git pull
            docker compose pull && docker compose up -d --build --remove-orphans
```

---

## 📂 Data Persistence

- `trades.db` – SQLite trade history
- `trades_log.csv` – flat‑file backup
  Stored in the Docker volume **`data`** so they survive container restarts.

---

## 🔒 Security Notes

- Never commit **.env** files – use `env_file` or Docker secrets.
- Use Binance **Testnet** (`TESTNET=True`) until you’re comfortable.
- This bot places real orders: **run at your own risk**.

---

## 📜 Licence & Disclaimer

MIT License.
The author is **not** responsible for financial losses. Trading crypto
involves significant risk.
