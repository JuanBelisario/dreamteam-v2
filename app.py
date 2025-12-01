# Dreamteam v3 – Multi-Moneda + Limpieza UI
import streamlit as st
import pandas as pd
import gspread
import time
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(page_title="Dreamteam v3", page_icon="💸", layout="centered")

# ---------- CONFIG / AUTH ----------
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_gspread_client():
    info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    return gspread.authorize(creds)

@st.cache_resource
def open_sheet():
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["general"]["sheet_id"])
    titles = [w.title for w in sh.worksheets()]
    if "transactions" not in titles:
        sh.add_worksheet("transactions", rows=2000, cols=15)
    if "config" not in titles:
        sh.add_worksheet("config", rows=10, cols=2)
    if "categories" not in titles:
        sh.add_worksheet("categories", rows=50, cols=1)
    return {
        "sh": sh,
        "tx": sh.worksheet("transactions"),
        "cfg": sh.worksheet("config"),
        "cat": sh.worksheet("categories"),
    }

# ---------- UTILS ----------
def _retry(fn, tries=3, delay=0.6):
    for i in range(tries):
        try:
            return fn()
        except APIError:
            if i == tries - 1:
                raise
            time.sleep(delay)

def ensure_headers(ws, headers):
    try:
        existing = _retry(lambda: ws.row_values(1))
    except Exception:
        return
    if not existing or existing != headers:
        try:
            _retry(lambda: ws.update("A1", [headers]))
        except Exception:
            pass

# ---------- DATA LOADERS ----------
@st.cache_data(ttl=300)
def read_config_data(cfg_values):
    if not cfg_values or len(cfg_values) < 2:
        return 0.6, 0.4
    df = pd.DataFrame(cfg_values[1:], columns=cfg_values[0])
    cfg = dict(zip(df["key"], df["value"]))
    sj = float(cfg.get("split_juan", 0.6))
    sm = float(cfg.get("split_mailu", 0.4))
    return sj, sm

@st.cache_data(ttl=300)
def read_categories_data(cat_values):
    cats = [r[0] for r in cat_values if r and str(r[0]).strip()]
    return cats if cats else ["Supermercado", "Comidas", "Hogar"]

@st.cache_data(ttl=120)
def read_transactions_data(tx_values):
    if not tx_values or len(tx_values) < 2:
        return pd.DataFrame(columns=[
            "timestamp","paid_by","category","amount","notes",
            "split_juan","split_mailu","amount_juan","amount_mailu","currency"
        ])
    df = pd.DataFrame(tx_values[1:], columns=tx_values[0])

    for col in ["amount", "amount_juan", "amount_mailu", "split_juan", "split_mailu"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    return df

def append_transaction(tx_ws, row_dict):
    headers = _retry(lambda: tx_ws.row_values(1))
    ordered = [row_dict.get(h, "") for h in headers]
    _retry(lambda: tx_ws.append_row(ordered, value_input_option="USER_ENTERED"))

# ---------- DEBT BY CURRENCY ----------
def compute_debt(df, currency, split_juan, split_mailu):
    d = df[df["currency"] == currency]
    if d.empty:
        return 0, 0, 0

    d_gasto = d.copy()

    d_gasto["paid_by_juan"] = (d_gasto["paid_by"].str.lower() == "juan").astype(float) * d_gasto["amount"]
    d_gasto["paid_by_mailu"] = (d_gasto["paid_by"].str.lower() == "mailu").astype(float) * d_gasto["amount"]

    d_gasto["owed_juan"] = d_gasto["amount"] * d_gasto["split_juan"]
    d_gasto["owed_mailu"] = d_gasto["amount"] * d_gasto["split_mailu"]

    juan_net = d_gasto["paid_by_juan"].sum() - d_gasto["owed_juan"].sum()
    mailu_net = d_gasto["paid_by_mailu"].sum() - d_gasto["owed_mailu"].sum()

    mailu_owes_juan = max(juan_net, 0)
    juan_owes_mailu = max(-juan_net, 0)

    return juan_net, mailu_net, mailu_owes_juan, juan_owes_mailu

# ---------- BOOTSTRAP ----------
if "sheets" not in st.session_state:
    st.session_state["sheets"] = open_sheet()
sheets = st.session_state["sheets"]

tx_ws = sheets["tx"]
cfg_ws = sheets["cfg"]
cat_ws = sheets["cat"]

TX_HEADERS = [
    "timestamp","paid_by","category","amount","notes",
    "split_juan","split_mailu","amount_juan","amount_mailu","currency"
]
if "tx_headers_ok" not in st.session_state:
    ensure_headers(tx_ws, TX_HEADERS)
    st.session_state["tx_headers_ok"] = True

cfg_values = cfg_ws.get_all_values()
cat_values = cat_ws.get_all_values()
tx_values = tx_ws.get_all_values()

split_juan, split_mailu = read_config_data(cfg_values)
categories = read_categories_data(cat_values)
df = read_transactions_data(tx_values)

# ---------- UI ----------
st.title("💸 Dreamteam v3")
st.subheader("Registrar gasto")

# ---- BALANCE PESOS ----
jn_p, mn_p, mop_p, _ = compute_debt(df, "ARS", split_juan, split_mailu)
if mop_p > 0:
    st.success(f"🇦🇷 En PESOS: Mailu debe a Juan ${mop_p:,.0f}")
elif jn_p < 0:
    st.success(f"🇦🇷 En PESOS: Juan debe a Mailu ${abs(jn_p):,.0f}")
else:
    st.info("🇦🇷 En PESOS están a mano.")

# ---- BALANCE USD ----
jn_u, mn_u, mop_u, _ = compute_debt(df, "USD", split_juan, split_mailu)
if mop_u > 0:
    st.success(f"💵 En DÓLARES: Mailu debe a Juan USD {mop_u:,.2f}")
elif jn_u < 0:
    st.success(f"💵 En DÓLARES: Juan debe a Mailu USD {abs(jn_u):,.2f}")
else:
    st.info("💵 En DÓLARES están a mano.")

st.write("---")

# ---- QUIÉN PAGÓ ----
paid_by = st.radio(
    "¿Quién pagó?",
    ["Juan", "Mailu"],
    horizontal=True
)

# ---- PORCENTAJE (solo input manual) ----
perc_juan = st.number_input(
    "% Juan",
    min_value=0,
    max_value=100,
    value=int(split_juan * 100),
    step=1
)
perc_mailu = 100 - perc_juan

st.caption(f"👉 Juan {perc_juan}% • Mailu {perc_mailu}%")

# ---- CATEGORÍA (solo selección, sin escribir) ----
category = st.selectbox(
    "Categoría",
    options=categories,
    index=0,
)

# ---- MONEDA ----
currency = st.radio("Moneda", ["ARS", "USD"], horizontal=True)

# ---- MONTO (sin decimales por defecto) ----
amount = st.number_input("Monto", min_value=0.0, value=0.0, step=1.0, format="%.0f")

# ---- NOTAS ----
notes = st.text_area("Notas")

# ---- GUARDAR ----
if st.button("Guardar gasto ✅", use_container_width=True):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    amount_juan = float(amount) * (perc_juan / 100)
    amount_mailu = float(amount) * (perc_mailu / 100)

    row = {
        "timestamp": ts,
        "paid_by": paid_by,
        "category": category,
        "amount": float(amount),
        "notes": notes,
        "split_juan": perc_juan / 100,
        "split_mailu": perc_mailu / 100,
        "amount_juan": amount_juan,
        "amount_mailu": amount_mailu,
        "currency": currency
    }

    append_transaction(tx_ws, row)
    st.success("Gasto registrado correctamente ✅")

st.caption("Editar categorías y splits desde el Sheet.")
