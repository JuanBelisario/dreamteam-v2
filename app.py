# Dreamteam v2 – Optimized Fast Load (with cache fix)
import streamlit as st
import pandas as pd
import gspread
import time
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(page_title="Dreamteam v2", page_icon="💸", layout="centered")

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
        sh.add_worksheet("transactions", rows=1000, cols=12)
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

# ---------- DATA LOADERS (CACHE FIX) ----------
@st.cache_data(ttl=300)
def read_config_data(cfg_values):
    """Lee config desde lista cacheable (no Worksheet)."""
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
    return cats if cats else ["Ingresos", "Supermercado", "Comidas"]

@st.cache_data(ttl=60)
def read_transactions_data(tx_values):
    if not tx_values or len(tx_values) < 2:
        return pd.DataFrame(columns=[
            "timestamp","entry_user","paid_by","paid_for","type","category",
            "amount","notes","split_juan","split_mailu","amount_juan","amount_mailu"
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

def compute_debt(df, default_split_juan=0.6, default_split_mailu=0.4):
    g = df[df["type"].str.lower() == "gasto"].copy()
    if g.empty:
        return 0.0, 0.0, 0.0
    def owed_parts(row):
        amt = float(row["amount"])
        pf = str(row.get("paid_for", "")).lower()
        sj = row.get("split_juan")
        sm = row.get("split_mailu")
        if pd.notna(sj) and pd.notna(sm) and pf == "ambos":
            return amt * float(sj), amt * float(sm)
        if pf == "ambos":
            return amt * default_split_juan, amt * default_split_mailu
        elif pf == "juan":
            return amt, 0.0
        elif pf == "mailu":
            return 0.0, amt
        return amt * default_split_juan, amt * default_split_mailu
    g["owed_juan"], g["owed_mailu"] = zip(*g.apply(owed_parts, axis=1))
    g["paid_by_juan"] = (g["paid_by"].str.lower() == "juan").astype(float) * g["amount"]
    g["paid_by_mailu"] = (g["paid_by"].str.lower() == "mailu").astype(float) * g["amount"]
    juan_net = g["paid_by_juan"].sum() - g["owed_juan"].sum()
    mailu_net = g["paid_by_mailu"].sum() - g["owed_mailu"].sum()
    mailu_owes_juan = max(0.0, juan_net)
    return juan_net, mailu_net, mailu_owes_juan

# ---------- BOOTSTRAP ----------
if "sheets" not in st.session_state:
    st.session_state["sheets"] = open_sheet()
sheets = st.session_state["sheets"]

tx_ws = sheets["tx"]
cfg_ws = sheets["cfg"]
cat_ws = sheets["cat"]

TX_HEADERS = [
    "timestamp","entry_user","paid_by","paid_for","type","category",
    "amount","notes","split_juan","split_mailu","amount_juan","amount_mailu"
]
if "tx_headers_ok" not in st.session_state:
    ensure_headers(tx_ws, TX_HEADERS)
    st.session_state["tx_headers_ok"] = True

# ---- Load data safely (now cached by value) ----
cfg_values = cfg_ws.get_all_values()
cat_values = cat_ws.get_all_values()
tx_values = tx_ws.get_all_values()

split_juan, split_mailu = read_config_data(cfg_values)
categories = read_categories_data(cat_values)
df = read_transactions_data(tx_values)

# ---------- REGISTRAR ----------
st.title("💸 Dreamteam v2")
st.subheader("Nuevo movimiento")

# Mostrar balance global arriba
juan_net, mailu_net, mailu_owes_juan = compute_debt(df, split_juan, split_mailu)
if mailu_owes_juan > 0:
    st.success(f"💚 Mailu le debe a Juan: **${mailu_owes_juan:,.0f}**")
elif juan_net < 0:
    st.success(f"💚 Juan le debe a Mailu: **${abs(juan_net):,.0f}**")
else:
    st.info("⚖️ Están a mano.")

colA, colB = st.columns(2)
with colA:
    st.caption("¿Quién está cargando ahora?")
    who_am_i = st.radio(
        "¿Quién está cargando ahora?",
        ["Juan", "Mailu"],
        horizontal=True,
        index=0,
        key="who_am_i_radio",
        label_visibility="collapsed",
    )
with colB:
    dt = st.date_input("Fecha", pd.Timestamp.now().date())

st.write("**¿Quién pagó?**")
paid_by = st.radio(
    "¿Quién pagó?",
    ["Juan", "Mailu"],
    horizontal=True,
    index=0,
    key="paid_by_radio",
    label_visibility="collapsed",
)

st.write("**¿Para quién fue?**")
paid_for = st.radio(
    "¿Para quién fue?**",
    ["Ambos", "Juan", "Mailu"],
    horizontal=True,
    index=0,
    key="paid_for_radio",
    label_visibility="collapsed",
)

# ---- Split por gasto (slider + input sincronizados) ----
st.markdown("### 💰 Distribución del gasto")

is_juan_payer = (paid_by == "Juan")
base_val = int(split_juan * 100) if is_juan_payer else int((1 - split_juan) * 100)
sv_key = "split_value"
if sv_key not in st.session_state:
    st.session_state[sv_key] = base_val

last_key = "last_payer"
if last_key not in st.session_state:
    st.session_state[last_key] = paid_by
if st.session_state[last_key] != paid_by:
    st.session_state[last_key] = paid_by
    st.session_state[sv_key] = base_val

col1, col2, col3 = st.columns([3, 1, 2])
with col1:
    slider_val = st.slider(
        f"{'Juan' if is_juan_payer else 'Mailu'} (%)",
        0, 100,
        st.session_state[sv_key],
        1,
        key=f"split_slider_{'J' if is_juan_payer else 'M'}",
        label_visibility="collapsed",
    )
with col2:
    input_val = st.number_input(
        "Editar %",
        0, 100,
        st.session_state[sv_key],
        1,
        key=f"split_input_{'J' if is_juan_payer else 'M'}",
        label_visibility="collapsed",
    )

if input_val != st.session_state[sv_key]:
    st.session_state[sv_key] = input_val
elif slider_val != st.session_state[sv_key]:
    st.session_state[sv_key] = slider_val

perc_value = st.session_state[sv_key]
if is_juan_payer:
    perc_juan = perc_value
    perc_mailu = 100 - perc_juan
else:
    perc_mailu = perc_value
    perc_juan = 100 - perc_mailu

with col3:
    st.markdown(
        f"<div style='text-align:left; line-height:1.4'>"
        f"<b>Juan:</b> {perc_juan}%<br>"
        f"<b>Mailu:</b> {perc_mailu}%"
        f"</div>",
        unsafe_allow_html=True,
    )

# ---- Tipo, categoría, monto y notas ----
st.markdown("### 📂 Tipo y categoría")
mtype = st.selectbox("Tipo", ["gasto", "ingreso"], index=0, key="mtype_select")
cat = st.selectbox("Categoría", categories, index=0, key="cat_select")
amount = st.number_input("Monto", min_value=0.0, step=0.01, format="%.2f")
notes = st.text_area("Notas")

if st.button("Guardar ✅", use_container_width=True):
    ts = datetime.combine(dt, datetime.min.time())
    amount_juan = float(amount) * (perc_juan / 100)
    amount_mailu = float(amount) * (perc_mailu / 100)
    row = {
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_user": who_am_i,
        "paid_by": paid_by,
        "paid_for": paid_for,
        "type": mtype,
        "category": cat,
        "amount": float(amount),
        "notes": notes,
        "split_juan": perc_juan / 100,
        "split_mailu": perc_mailu / 100,
        "amount_juan": amount_juan,
        "amount_mailu": amount_mailu,
    }
    append_transaction(tx_ws, row)
    st.success("Movimiento registrado ✅")

st.divider()
st.caption("Podés editar categorías y splits desde el Sheet directamente.")
