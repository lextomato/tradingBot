# =============================
# dashboard.py (con métricas de exposición)
# =============================

"""
Dashboard Streamlit para visualizar las operaciones del Grid Bot + exposición actual en USDT.
"""

import time
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import matplotlib.pyplot as plt
from binance.client import Client
from dotenv import load_dotenv
import os

# ---------------- Config ----------------
load_dotenv(override=True)
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")
TESTNET = os.getenv("TESTNET")

client = Client(API_KEY, API_SECRET, testnet=(TESTNET == "True"))
if TESTNET == "True":
    client.API_URL = "https://testnet.binance.vision/api"

DB_PATH = "trades.db"
REFRESH_EVERY = 30  # segundos

st.set_page_config(page_title="Grid Bot Dashboard", layout="wide")
st.title("📊 Grid Trading Bot – Dashboard")
count = st_autorefresh(interval=REFRESH_EVERY * 1000, key="refresh")

if not Path(DB_PATH).exists():
    st.warning("Aún no se ha generado el archivo trades.db. Corre primero el bot.")
    st.stop()

# ---------------- Cargar datos de operaciones ----------------
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("SELECT * FROM trades", conn, parse_dates=["ts"])
conn.close()

# ---------------- Exposición actual ----------------
def get_exposure():
    eth = float(client.get_asset_balance(asset="ETH")["free"])
    eth_locked = float(client.get_asset_balance(asset="ETH")["locked"])
    price = float(client.get_symbol_ticker(symbol="ETHUSDT")["price"])
    usdt_locked = float(client.get_asset_balance(asset="USDT")["locked"])
    
    eth_total = eth + eth_locked
    return {
        "ETH disponible": eth,
        "ETH bloqueado": eth_locked,
        "ETH total": eth_total,
        "Precio actual ETH": price,
        "Valor ETH total (USDT)": eth_total * price,
        "USDT bloqueado en órdenes": usdt_locked,
        "Exposición total": eth_total * price + usdt_locked,
    }

# ---------------- Mostrar métricas ----------------
exposure = get_exposure()

# primera fila
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total trades", len(df))
col2.metric("PnL total (USDT)", f"{df['pnl'].sum():.2f}")
win_rate = len(df[df["pnl"] > 0]) / len(df) * 100
col3.metric("Win-rate", f"{win_rate:.1f}%")
col4.metric("Última operación", df.iloc[-1]["ts"].strftime("%Y-%m-%d %H:%M:%S"))

# segunda fila – todas las exposiciones
c5, c6, c7, c8 = st.columns(4)
c5.metric("ETH disponible", f"{exposure['ETH disponible']:.5f}")
c6.metric("ETH bloqueado",  f"{exposure['ETH bloqueado']:.5f}")
c7.metric("ETH total",      f"{exposure['ETH total']:.5f}")
c8.metric("Precio ETH",     f"{exposure['Precio actual ETH']:.2f} USDT")

# tercera fila – valoraciones en USDT
c9, c10 = st.columns(2)
c9.metric("USDT bloqueado en órdenes", f"{exposure['USDT bloqueado en órdenes']:.2f}")
c10.metric("Exposición total",          f"{exposure['Exposición total']:.2f} USDT")

# ---------------- Gráfico PnL acumulado ----------------
df["cum_pnl"] = df["pnl"].cumsum()
fig, ax = plt.subplots()
ax.plot(df["ts"], df["cum_pnl"], label="PnL acumulado")
ax.set_xlabel("Fecha")
ax.set_ylabel("USDT")
ax.legend()
st.pyplot(fig)

# ---------------- Tabla operaciones ----------------
st.subheader("Historial de operaciones")
st.dataframe(df.sort_values("ts", ascending=False), use_container_width=True)
