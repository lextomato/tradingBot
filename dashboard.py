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

load_dotenv(override=True)
# -------------------------------------------------
# Lee la misma configuración que usa el bot
# -------------------------------------------------
SYMBOL  = os.getenv("SYMBOL", "ETHUSDT")
BASE_ASSET = SYMBOL[:-4]        # p.e.  ETH  de  ETHUSDT
DATA_DIR   = os.getenv("DATA_DIR", "./data")

# ---------------- Config ----------------
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("SECRET_KEY")
TESTNET = os.getenv("TESTNET")

client = Client(API_KEY, API_SECRET, testnet=(TESTNET == "True"))
if TESTNET == "True":
    client.API_URL = "https://testnet.binance.vision/api"

DB_PATH = os.path.join(DATA_DIR, "trades.db")
REFRESH_EVERY = 30  # segundos

st.set_page_config(page_title="Grid Bot Dashboard", layout="wide")
st.title("📊 Grid Trading Bot – Dashboard")
count = st_autorefresh(interval=REFRESH_EVERY * 1000, key="refresh")

# ---------------- Verificación de base de datos ----------------
if not Path(DB_PATH).exists():
    st.warning("Aún no se ha generado el archivo trades.db. Corre primero el bot.")
    st.stop()

# ---------------- Cargar datos de operaciones ----------------
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("SELECT * FROM trades", conn, parse_dates=["ts"])
cur = conn.cursor()
cur.execute("SELECT value FROM state WHERE key='eth_bot_balance'")
row = cur.fetchone()
ETH_BALANCE_BOT = 0.0
if row:
    ETH_BALANCE_BOT = float(row[0])
conn.close()

# ---------------- Control del Bot ----------------
st.sidebar.title("🛠 Control del Bot")
stop_file = "STOP.txt"

if st.sidebar.button("⏹️ Detener bot"):
    with open(stop_file, "w") as f:
        f.write("pause")
    st.sidebar.success("Bot detenido.")

if st.sidebar.button("▶️ Iniciar bot"):
    if os.path.exists(stop_file):
        os.remove(stop_file)
    st.sidebar.success("Bot reactivado.")

estado = "Detenido" if os.path.exists(stop_file) else "Activo"
st.sidebar.markdown(f"### Estado actual: **{estado}**")

# Saldo controlado por el bot (leer del estado)
st.sidebar.markdown(
    f"### {BASE_ASSET} balance Bot: **{ETH_BALANCE_BOT:.5f}**"
)

# ---------------- Exposición actual ----------------
def get_exposure():
    base = float(client.get_asset_balance(asset=BASE_ASSET)["free"])
    base_locked = float(client.get_asset_balance(asset=BASE_ASSET)["locked"])
    price = float(client.get_symbol_ticker(symbol=SYMBOL)["price"])
    usdt_locked = float(client.get_asset_balance(asset="USDT")["locked"])

    base_total = base + base_locked
    return {
        f"{BASE_ASSET} disponible": base,
        f"{BASE_ASSET} bloqueado":  base_locked,
        f"{BASE_ASSET} total":      base_total,
        f"Precio actual {BASE_ASSET}": price,
        "Valor base total (USDT)":      base_total * price,
        "USDT bloqueado en órdenes": usdt_locked,
        "Exposición total": base_total * price + usdt_locked,
    }

# ---------------- Mostrar métricas ----------------
exposure = get_exposure()

# primera fila
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total trades", len(df))
col2.metric("PnL total (USDT)", f"{df['pnl'].sum():.2f}")
win_rate = (len(df[df["pnl"] > 0]) / len(df) * 100) if len(df) else 0
col3.metric("Win-rate", f"{win_rate:.1f}%")
col4.metric("Última operación", df.iloc[-1]["ts"].strftime("%Y-%m-%d %H:%M:%S"))

# segunda fila – exposiciones base
c5, c6, c7, c8 = st.columns(4)
c5.metric(f"{BASE_ASSET} disponible", f"{exposure[f'{BASE_ASSET} disponible']:.5f}")
c6.metric(f"{BASE_ASSET} bloqueado",  f"{exposure[f'{BASE_ASSET} bloqueado']:.5f}")
c7.metric(f"{BASE_ASSET} total",      f"{exposure[f'{BASE_ASSET} total']:.5f}")
c8.metric(f"Precio {BASE_ASSET}",     f"{exposure[f'Precio actual {BASE_ASSET}']:.2f} USDT")

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
