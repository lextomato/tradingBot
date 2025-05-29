# =============================
# grid_trading_bot_v2a.py
# =============================

"""
Cambios clave (v2-A):
1. Grid uniforme: tamaño = (upper - lower) / grids
2. Capital total: si TOTAL_USDT > 0 reparte entre grids.
3. Valida LOT_SIZE y MIN_NOTIONAL; ajusta o advierte.
"""

import os, time, math, csv, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
import json

# ---------- Configuración ---------- #
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

API_KEY    = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")
TESTNET    = os.getenv("TESTNET") == "True"

SYMBOL            = os.getenv("SYMBOL", "ETHUSDT")
SPREAD_USD        = float(os.getenv("SPREAD_USD", 35))      # ±
GRIDS             = int(os.getenv("GRIDS", 16))             # nº de niveles
TOTAL_USDT        = float(os.getenv("TOTAL_USDT", 230))     # capital global
USDT_PER_ORDER    = float(os.getenv("USDT_PER_ORDER", 10))  # fallback
TARGET_GAIN_PCT   = float(os.getenv("TARGET_GAIN_PCT", 0.015))
FEE_PCT           = float(os.getenv("FEE_PCT", 0.001))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", 0.02))
STOP_LOSS_PCT     = float(os.getenv("STOP_LOSS_PCT", 0.10))

DATA_DIR = os.getenv("DATA_DIR", "./data")
Path(DATA_DIR).mkdir(exist_ok=True)
DB_PATH  = os.path.join(DATA_DIR, "trades.db")
CSV_PATH = os.path.join(DATA_DIR, "trades_log.csv")
# ----------------------------------- #

class GridTrader:
    def __init__(self, client: Client):
        self.client = client
        self.symbol = SYMBOL
        self.spread_usd = SPREAD_USD
        self.grids = GRIDS
        self.total_usdt = TOTAL_USDT if TOTAL_USDT > 0 else None
        self.usdt_per_order = self.total_usdt / self.grids if self.total_usdt else USDT_PER_ORDER
        self.target_gain_pct = TARGET_GAIN_PCT
        self.fee_pct = FEE_PCT
        self.trailing_stop_pct = TRAILING_STOP_PCT
        self.stop_loss_pct = STOP_LOSS_PCT
        self.db_path = DB_PATH
        self.csv_path = CSV_PATH

        # estado
        self.active_grid = {}
        self.highest_price = 0.0
        self.eth_bot_balance = 0.0

        self._init_db()
        self._init_csv()
        self._load_state()

        # obtén filtros de Binance
        self._load_filters()

        # rango inicial y grid size uniforme
        price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])
        self.lower = price - self.spread_usd
        self.upper = price + self.spread_usd
        self.grid_size = (self.upper - self.lower) / self.grids
        # si grid_size muy pequeño y viola min_notional, reajusta
        self._sanity_adjust_grids(price)

    # ---------- Binance filters ---------- #
    def _load_filters(self):
        info = self.client.get_symbol_info(self.symbol)
        filters = {f["filterType"]: f for f in info["filters"]}
        # imprimir filtros para debug formateado para visualizcion en consola de forma ordenada con identacion
        print("Binance filters:\n", json.dumps(filters, indent=2, sort_keys=True))
        self.lot_size   = float(filters["LOT_SIZE"]["stepSize"])
        self.min_qty    = float(filters["LOT_SIZE"]["minQty"])
        self.min_notional = float(filters["NOTIONAL"]["minNotional"])

    # ---------- persistencia ---------- #
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, side TEXT, price REAL, qty REAL, pnl REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL)")
        conn.commit(); conn.close()

    def _init_csv(self):
        if not os.path.isfile(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                csv.writer(f).writerow(["ts", "side", "price", "qty", "pnl"])

    def _save_state(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("REPLACE INTO state (key,value) VALUES ('eth_bot_balance',?)", (self.eth_bot_balance,))
        conn.commit(); conn.close()

    def _load_state(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT value FROM state WHERE key='eth_bot_balance'")
        row = cur.fetchone()
        if row: self.eth_bot_balance = float(row[0])
        conn.close()

    # ---------- helpers ---------- #
    def _adjust_qty(self, qty):
        precision = int(-math.log10(self.lot_size))
        adjusted = math.floor(qty / self.lot_size) * self.lot_size
        return float(f"{adjusted:.{precision}f}")

    def _log_trade(self, side, price, qty, pnl=0.0):
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.csv_path, "a", newline="") as f:
            csv.writer(f).writerow([ts, side, price, qty, pnl])
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?)", (ts, side, price, qty, pnl))
        conn.commit(); conn.close()

    # ---------- grid operaciones ---------- #
    def _sanity_adjust_grids(self, price_now: float) -> None:
        """
        Ajusta self.grids para que:
        1. Cada orden cumpla LOT_SIZE y MIN_NOTIONAL de Binance.
        2. El tamaño del grid (gap) sea, al menos, igual a 2 × fee_pct
            → de ese modo cubre las comisiones maker+maker o taker+taker.
        """
        # Capital total real que se va repartiendo al reducir niveles
        base_capital = self.total_usdt or (self.usdt_per_order * self.grids)
        original_grids = self.grids

        while True:
            # --------------- chequeo de filtros ---------------
            nominal = self.usdt_per_order
            qty = nominal / price_now
            filters_ok = (nominal >= self.min_notional) and (qty >= self.min_qty)

            # --------------- chequeo de comisiones ------------
            gap_pct = self.grid_size / price_now        # porcentaje del salto
            fee_need = 2 * self.fee_pct                 # comisión ida + vuelta
            gap_ok = gap_pct >= fee_need

            if filters_ok and gap_ok:
                break   # todo correcto

            # Si no se cumple, reducen niveles
            if self.grids <= 1:
                raise ValueError(
                    "Rango ±SPREAD_USD demasiado estrecho o capital insuficiente "
                    "para cubrir MIN_NOTIONAL y/o las comisiones."
                )

            self.grids -= 1
            self.grid_size = (self.upper - self.lower) / self.grids
            self.usdt_per_order = base_capital / self.grids

        # Mensaje informativo si se tuvo que ajustar
        if self.grids < original_grids:
            print(
                f"[Ajuste] Grids reducidos a {self.grids} "
                f"→ gap = {self.grid_size:.2f} USDT "
                f"({gap_pct*100:.3f} %) ≥ 2×fee_pct."
            )


    def _place_limit(self, side, price, qty):
        try:
            return self.client.create_order(
                symbol=self.symbol,
                side=side, type=ORDER_TYPE_LIMIT, timeInForce=TIME_IN_FORCE_GTC,
                quantity=self._adjust_qty(qty), price=f"{price:.2f}"
            )["orderId"]
        except BinanceAPIException as e:
            print("Limit order error:", e); return None

    def _cancel_order(self, oid):
        try: self.client.cancel_order(symbol=self.symbol, orderId=oid)
        except BinanceAPIException as exc:
            if exc.code not in (-2011,): print("Cancel error:", exc)

    def _equity(self):
        bal = {b["asset"]: float(b["free"]) + float(b["locked"])
               for b in self.client.get_account()["balances"]}
        price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])
        return bal.get("USDT", 0) + bal.get("ETH", 0) * price

    def setup_grid(self):
        # cancela cualquier orden previa
        for o in self.client.get_open_orders(symbol=self.symbol):
            self._cancel_order(o["orderId"])
        self.active_grid.clear()

        level_price = self.lower
        while level_price < self.upper - 1e-8:   # deja hueco al upper
            level = round(level_price, 2)
            self.active_grid[level] = {
                "buy_price": level,
                "sell_price": round(level + self.grid_size, 2),
                "order_id": None,
                "status": "EMPTY"
            }
            level_price += self.grid_size

        # coloca las órdenes BUY
        for lvl, node in self.active_grid.items():
            qty = self.usdt_per_order / lvl
            oid = self._place_limit(SIDE_BUY, lvl, qty)
            if oid:
                node.update(order_id=oid, status="BUY_PLACED")
                print(f"Buy limit placed @{lvl} ({qty:.5f} ETH)")

    def close_all(self):
        for o in self.client.get_open_orders(symbol=self.symbol):
            self._cancel_order(o["orderId"])
        if self.eth_bot_balance > 0:
            qty = self._adjust_qty(self.eth_bot_balance)
            try:
                self.client.create_order(symbol=self.symbol, side=SIDE_SELL,
                                         type=ORDER_TYPE_MARKET, quantity=qty)
                print(f"Market sold {qty} ETH (bot balance)")
            except BinanceAPIException as e:
                print("Error selling bot balance:", e)
        self.eth_bot_balance = 0.0; self._save_state()

    # ---------- bucle principal ---------- #
    def run(self, poll=10):
        print("Grid bot running…  (Ctrl-C para salir)")
        initial_equity = self._equity()
        self.highest_price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])

        while True:
            try:
                price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])
                self.highest_price = max(self.highest_price, price)

                # stop-loss global
                if self._equity() < initial_equity * (1 - self.stop_loss_pct):
                    print("Global stop-loss triggered.")
                    self.close_all(); break

                # recorre nodos
                for lvl, node in list(self.active_grid.items()):
                    oid, status = node["order_id"], node["status"]
                    if status == "BUY_PLACED":
                        order = self.client.get_order(symbol=self.symbol, orderId=oid)
                        if order["status"] == "FILLED":
                            qty = float(order["executedQty"])
                            self.eth_bot_balance += qty; self._save_state()
                            self._log_trade("BUY", lvl, qty)
                            sell_oid = self._place_limit(SIDE_SELL, node["sell_price"], qty)
                            node.update(order_id=sell_oid, status="SELL_PLACED")
                            print(f"Filled BUY @{lvl}, placed SELL @{node['sell_price']}")
                    elif status == "SELL_PLACED":
                        order = self.client.get_order(symbol=self.symbol, orderId=oid)
                        if order["status"] == "FILLED":
                            qty = float(order["executedQty"])
                            buy_fee  = node["buy_price"]  * self.fee_pct
                            sell_fee = node["sell_price"] * self.fee_pct
                            pnl = (node["sell_price"] - sell_fee) - (node["buy_price"] + buy_fee)
                            pnl *= qty
                            self.eth_bot_balance -= qty; self._save_state()
                            self._log_trade("SELL", node["sell_price"], qty, pnl)
                            # recoloca BUY
                            new_qty = self.usdt_per_order / node["buy_price"]
                            buy_oid = self._place_limit(SIDE_BUY, node["buy_price"], new_qty)
                            node.update(order_id=buy_oid, status="BUY_PLACED")
                            print(f"Filled SELL @{node['sell_price']}  PnL={pnl:.2f} USDT")

                # trailing-stop de tendencia
                if price < self.highest_price * (1 - self.trailing_stop_pct):
                    print("Trailing stop reset del grid…")
                    self.close_all()
                    self.lower = price - self.spread_usd
                    self.upper = price + self.spread_usd
                    self.grid_size = (self.upper - self.lower) / self.grids
                    self.setup_grid()
                    initial_equity = self._equity()
                    self.highest_price = price

                time.sleep(poll)

            except KeyboardInterrupt:
                print("Interrumpido por el usuario.")
                self.close_all(); break
            except Exception as e:
                print("Loop error:", e); time.sleep(poll)

# ---------- ejecución ---------- #
def main():
    if not API_KEY or not API_SECRET:
        raise SystemExit("Configura variables API_KEY y SECRET_KEY en .env")

    client = Client(API_KEY, API_SECRET, testnet=TESTNET)
    if TESTNET:
        client.API_URL = "https://testnet.binance.vision/api"

    bot = GridTrader(client)
    bot.setup_grid()
    bot.run()

if __name__ == "__main__":
    main()
