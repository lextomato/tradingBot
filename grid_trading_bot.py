# =============================
# grid_trading_bot.py (versión mejorada)
# =============================

"""
Mejoras:
1. Ajuste dinámico del rango de trading con base en precio actual ±50 USD.
2. Inclusión de comisiones spot (0.1%) en el cálculo de PnL neto.
3. Cálculo automático de grid_size según rentabilidad objetivo (por orden).
"""

import os
import time
import math
import csv
import sqlite3
from datetime import datetime, timezone
from os import getenv
from dotenv import load_dotenv
from pprint import pprint
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

API_KEY = getenv("API_KEY")
API_SECRET = getenv("SECRET_KEY")
TESTNET = getenv("TESTNET")

from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

class GridTrader:
    def __init__(
        self,
        client: Client,
        symbol: str,
        spread_usd: float = 50,
        grids: int = 10,
        usdt_per_order: float = 10,
        target_gain_pct: float = 0.015,
        fee_pct: float = 0.001,
        trailing_stop_pct: float = 0.02,
        stop_loss_pct: float = 0.10,
        db_path: str = "trades.db",
        csv_path: str = "trades_log.csv",
    ):
        self.client = client
        self.symbol = symbol
        self.grids = grids
        self.usdt_per_order = usdt_per_order
        self.target_gain_pct = target_gain_pct
        self.fee_pct = fee_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.stop_loss_pct = stop_loss_pct
        self.db_path = db_path
        self.csv_path = csv_path

        # Rango dinámico basado en precio actual ± spread
        current_price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        self.lower = current_price - spread_usd
        self.upper = current_price + spread_usd
        self.grid_size = self._calculate_grid_size(current_price)

        self.step_size = self._get_step_size()
        self.active_grid = {}
        self.highest_price = 0.0

        self._init_db()
        self._init_csv()

    def _calculate_grid_size(self, price):
        # tamaño de grid que incluye target de ganancia y comisiones round trip
        return price * (self.target_gain_pct + 2 * self.fee_pct)

    def _get_step_size(self):
        info = self.client.get_symbol_info(self.symbol)
        lot_size = [f for f in info["filters"] if f["filterType"] == "LOT_SIZE"][0]
        return float(lot_size["stepSize"])

    def _adjust_qty(self, qty):
        precision = int(-math.log10(self.step_size))
        adjusted = math.floor(qty / self.step_size) * self.step_size
        return float(f"{adjusted:.{precision}f}")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS trades (
                ts TEXT,
                side TEXT,
                price REAL,
                qty REAL,
                pnl REAL
            )"""
        )
        conn.commit()
        conn.close()

    def _init_csv(self):
        if not os.path.isfile(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                csv.writer(f).writerow(["ts", "side", "price", "qty", "pnl"])

    def _log_trade(self, side, price, qty, pnl=0.0):
        ts = datetime.now(timezone.utc).isoformat()
        with open(self.csv_path, "a", newline="") as f:
            csv.writer(f).writerow([ts, side, price, qty, pnl])
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?)", (ts, side, price, qty, pnl))
        conn.commit()
        conn.close()

    def _place_limit(self, side, price, qty):
        try:
            order = self.client.create_order(
                symbol=self.symbol,
                side=side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=self._adjust_qty(qty),
                price=f"{price:.2f}",
            )
            return order["orderId"]
        except Exception as e:
            print("Limit order error:", e)
            return None

    def _cancel_order(self, order_id):
        try:
            self.client.cancel_order(symbol=self.symbol, orderId=order_id)
        except BinanceAPIException as exc:
            if exc.code not in (-2011,):
                print("Cancel error:", exc)

    def _equity(self):
        balances = self.client.get_account()["balances"]
        bal = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in balances}
        price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])
        return bal.get("USDT", 0) + bal.get("ETH", 0) * price

    def setup_grid(self):
        for o in self.client.get_open_orders(symbol=self.symbol):
            self._cancel_order(o["orderId"])

        self.active_grid.clear()
        level_price = self.lower
        while level_price < self.upper:
            level = round(level_price, 2)
            self.active_grid[level] = {
                "buy_price": level,
                "sell_price": round(level + self.grid_size, 2),
                "order_id": None,
                "status": "EMPTY",
            }
            level_price += self.grid_size

        for level, node in self.active_grid.items():
            qty = self.usdt_per_order / level
            order_id = self._place_limit(SIDE_BUY, level, qty)
            if order_id:
                node["order_id"] = order_id
                node["status"] = "BUY_PLACED"
                print(f"Buy limit placed @{level} ({qty:.5f} ETH)")

    def close_all(self):
        for o in self.client.get_open_orders(symbol=self.symbol):
            self._cancel_order(o["orderId"])
        eth_bal = float(self.client.get_asset_balance(asset="ETH")["free"])
        if eth_bal > 0:
            eth_bal = self._adjust_qty(eth_bal)
            self.client.create_order(
                symbol=self.symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=eth_bal,
            )
            print(f"Market sold remaining {eth_bal} ETH")

    def run(self, poll=15):
        print("Grid bot running…")
        initial_equity = self._equity()
        while True:
            try:
                price = float(self.client.get_symbol_ticker(symbol=self.symbol)["price"])
                self.highest_price = max(self.highest_price, price)

                if self._equity() < initial_equity * (1 - self.stop_loss_pct):
                    print("Global stop-loss hit; closing bot.")
                    self.close_all()
                    break

                for level, node in list(self.active_grid.items()):
                    status = node["status"]
                    oid = node["order_id"]
                    if status == "BUY_PLACED":
                        order = self.client.get_order(symbol=self.symbol, orderId=oid)
                        if order["status"] == "FILLED":
                            qty = float(order["executedQty"])
                            self._log_trade("BUY", level, qty)
                            sell_oid = self._place_limit(SIDE_SELL, node["sell_price"], qty)
                            node.update(order_id=sell_oid, status="SELL_PLACED")
                            print(f"Filled BUY @{level}, placed SELL @{node['sell_price']}")
                    elif status == "SELL_PLACED":
                        order = self.client.get_order(symbol=self.symbol, orderId=oid)
                        if order["status"] == "FILLED":
                            qty = float(order["executedQty"])
                            # PnL neto considerando comisión
                            buy_price = node["buy_price"] * (1 + self.fee_pct)
                            sell_price = node["sell_price"] * (1 - self.fee_pct)
                            pnl = (sell_price - buy_price) * qty
                            self._log_trade("SELL", node["sell_price"], qty, pnl)
                            qty = self.usdt_per_order / node["buy_price"]
                            buy_oid = self._place_limit(SIDE_BUY, node["buy_price"], qty)
                            node.update(order_id=buy_oid, status="BUY_PLACED")
                            print(f"Filled SELL @{node['sell_price']}, new BUY @{node['buy_price']}")

                if price < self.highest_price * (1 - self.trailing_stop_pct):
                    print("Trailing stop hit; resetting grid…")
                    self.close_all()
                    self.highest_price = price
                    self.setup_grid()
                    initial_equity = self._equity()

                time.sleep(poll)
            except Exception as e:
                print("Loop error:", e)
                time.sleep(poll)


def main():
    if not API_KEY or not API_SECRET:
        raise SystemExit("Configura las variables API_KEY y SECRET_KEY")

    client = Client(API_KEY, API_SECRET, testnet=True)
    if TESTNET == "True":
        client.API_URL = "https://testnet.binance.vision/api"

    print_balance(client, "USDT")
    print_balance(client, "ETH")

    bot = GridTrader(
        client,
        symbol="ETHUSDT",
        spread_usd=30,
        grids=10,
        usdt_per_order=30,
        target_gain_pct=0.015,
        fee_pct=0.001,
    )
    bot.setup_grid()
    bot.run()

def print_balance(client, asset_symbol):
    # Obtén la información de la cuenta
    account_info = client.get_account()
    balances = account_info['balances']
    
    # Busca y muestra el balance de la moneda especificada
    for balance in balances:
        if balance['asset'] == asset_symbol:
            print(f"{balance['asset']}: {balance['free']} disponible")
            break
    else:
        print(f"No se encontró saldo para {asset_symbol}")


if __name__ == "__main__":
    main()
