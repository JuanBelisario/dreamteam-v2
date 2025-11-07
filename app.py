#reap22
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(page_title="Dreamteam v2", page_icon="💸", layout="centered")

# ---------- CONFIG / AUTH ----------
SCOPE = ["https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive"]

@st.cache_resource
def get_gspread_client():
    info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    return gspread.authorize(creds)

@st.cache_resource
def open_sheet():
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["general"]["sheet_id"])
    return {
        "sh": sh,
        "tx": sh.worksheet("transactions"),
        "cfg": sh.worksheet("config"),
        "cat": sh.worksheet("categories"),
    }

def ensure_headers(ws, headers):
    existing = ws.row_values(1)
    if existing != headers:
        ws.clear()
        ws.append_row(headers)

def read_config(cfg_ws):
    data = cfg_ws.get_all_records()
    cfg = {row["key"]: row["value"] for row in data}
    # Fallbacks
    sj = float(cfg.get("split_juan", 0.6))
    sm = float(cfg.get("split_mailu", 0.4))
    return sj, sm

def read_categories(cat_ws):
    cats = [row[0] for row in cat_ws.get_all_values() if row]
    # Asegura lista mínima
    return cats if cats else ["Ingresos", "Supermercado", "Comidas"]

def read_transactions(tx_ws):
    vals = tx_ws.get_all_values()
    if not vals or len(vals) < 2:
        return pd.DataFrame(columns=["timestamp","entry_user","paid_by","paid_for","type","category","amount","notes"])
    df = pd.DataFrame(vals[1:], columns=vals[0])
    # Tipar
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    if "timestamp" in df.columns:
        # soporta timestamp y date
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df

def append_transaction(tx_ws, row):
    tx_ws.append_row(row)

def month_filter(df, dt=None):
    dt = dt or pd.Timestamp.now()
    return df[(df["timestamp"].dt.year == dt.year) & (df["timestamp"].dt.month == dt.month)]

# ---------- DEUDA / SPLIT ----------
def compute_debt(df, split_juan=0.6, split_mailu=0.4):
    # Solo gastos influyen en deuda
    g = df[df["type"].str.lower() == "gasto"].copy()
    if g.empty:
        return 0.0, 0.0, 0.0  # juan_net, mailu_net, mailu_owes_juan

    def owed_parts(row):
        amt = float(row["amount"])
        pf = row["paid_for"].lower()
        if pf == "ambos":
            return amt * split_juan, amt * split_mailu
        elif pf == "juan":
            return amt, 0.0
        elif pf == "mailu":
            return 0.0, amt
        else:
            # fallback: ambos
            return amt * split_juan, amt * split_mailu

    g["owed_juan"], g["owed_mailu"] = zip(*g.apply(owed_parts, axis=1))
    g["paid_by_juan"] = g["paid_by"].str.lower().eq("juan").astype(float) * g["amount"]
    g["paid_by_mailu"] = g["paid_by"].str.lower().eq("mailu").astype(float) * g["amount"]

    # Netos: lo que pagó menos lo que debía pagar
    juan_net = g["paid_by_juan"].sum() - g["owed_juan"].sum()
    mailu_net = g["paid_by_mailu"].sum() - g["owed_mailu"].sum()

    # Si juan_net>0, significa que Juan puso de más y Mailu le debe esa diferencia (idealmente igual a -mailu_net)
    mailu_owes_juan = max(0.0, juan_net)  # clamp
    return juan_net, mailu_net, mailu_owes_juan

# ---------- UI ----------
st.title("💸 Dreamteam v2")

sheets = open_sheet()
tx_ws = sheets["tx"]
cfg_ws = sheets["cfg"]
cat_ws = sheets["cat"]

# Asegura encabezados en transactions
ensure_headers(tx_ws, ["timestamp","entry_user","paid_by","paid_for","type","category","amount","notes"])

# Lee config y categorías
split_juan, split_mailu = read_config(cfg_ws)
categories = read_categories(cat_ws)

# ---- NAV ----
page = st.sidebar.radio("Navegación", ["➕ Registrar", "📊 Dashboard", "⚙️ Config"])

if page == "➕ Registrar":
    st.subheader("Nuevo movimiento")
    colA, colB = st.columns(2)
    with colA:
        # Botón rápido: quién está cargando
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

    # Permitir cargar gastos del otro:
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
    "¿Para quién fue?",
    ["Ambos", "Juan", "Mailu"],
    horizontal=True,
    index=0,
    key="paid_for_radio",
    label_visibility="collapsed",
)
     # ---- Split personalizado por gasto ----
st.write("**Distribución del gasto**")
colj, colm = st.columns(2)
with colj:
    perc_juan = st.slider(
        "Juan (%)",
        min_value=0,
        max_value=100,
        value=int(split_juan * 100),
        step=5,
        key="juan_pct",
    )
with colm:
    perc_mailu = 100 - perc_juan
    st.metric("Mailu (%)", f"{perc_mailu}%")
    
    mtype = st.selectbox("Tipo", ["gasto", "ingreso"])
    cat = st.selectbox("Categoría", categories)
    amount = st.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
    notes = st.text_input("Notas", "")

    if st.button("Guardar ✅", use_container_width=True):
    ts = datetime.combine(dt, datetime.min.time())
    amount_juan = amount * (perc_juan / 100)
    amount_mailu = amount * (perc_mailu / 100)

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
        "amount_mailu": amount_mailu
    }
    append_transaction(row)
    st.success("Movimiento registrado.")

    st.divider()
    st.caption("Tip: Podés agregar o quitar categorías desde la pestaña `categories` en el Sheet.")

elif page == "📊 Dashboard":
    st.subheader("Resumen mensual")

    df = read_transactions(tx_ws)
    if df.empty:
        st.info("Aún no hay movimientos.")
    else:
        # Filtros
        month = st.selectbox(
            "Mes",
            sorted(df["timestamp"].dt.to_period("M").astype(str).unique()),
            index=len(df["timestamp"].dt.to_period("M").unique()) - 1
        )
        # Filtro por mes elegido
        y, m = map(int, month.split("-"))
        dff = df[(df["timestamp"].dt.year == y) & (df["timestamp"].dt.month == m)].copy()

        # KPIs
        gastos = dff[dff["type"].str.lower() == "gasto"]["amount"].sum()
        ingresos = dff[dff["type"].str.lower() == "ingreso"]["amount"].sum()
        ahorro = ingresos - gastos
        st.metric("Gastos del mes", f"${gastos:,.0f}")
        st.metric("Ingresos del mes", f"${ingresos:,.0f}")
        st.metric("Ahorro (ingresos - gastos)", f"${ahorro:,.0f}")

        # Deuda neta (sobre TODOS los datos; o si preferís solo mes, cambialo a dff)
        juan_net, mailu_net, mailu_owes_juan = compute_debt(df, split_juan, split_mailu)
        if mailu_owes_juan > 0:
            st.success(f"💚 Mailu le debe a Juan: **${mailu_owes_juan:,.0f}**")
        elif juan_net < 0:
            st.success(f"💚 Juan le debe a Mailu: **${abs(juan_net):,.0f}**")
        else:
            st.info("⚖️ Están a mano.")

        st.divider()
        # Gastos por categoría (mes)
        g_mes = dff[dff["type"].str.lower() == "gasto"].groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False)
        if not g_mes.empty:
            st.bar_chart(g_mes.set_index("category"))
        else:
            st.caption("No hay gastos este mes.")

        st.divider()
        st.write("Últimos movimientos del mes")
        show = dff.sort_values("timestamp", ascending=False)
        st.dataframe(show, use_container_width=True, hide_index=True)

elif page == "⚙️ Config":
    st.subheader("Split de gastos")
    st.caption("Estos valores también se pueden editar directamente en la pestaña `config` del Google Sheet.")
    sj = st.number_input("Split Juan (%)", min_value=0.0, max_value=1.0, step=0.05, value=float(split_juan))
    sm = st.number_input("Split Mailu (%)", min_value=0.0, max_value=1.0, step=0.05, value=float(split_mailu))
    if abs((sj + sm) - 1.0) > 1e-9:
        st.error("La suma debe ser 1.0 (100%).")
    else:
        if st.button("Guardar split", use_container_width=True):
            # Reescribe config
            cfg_ws.update("A1:B3", [
                ["key","value"],
                ["split_juan", sj],
                ["split_mailu", sm]
            ])
            st.success("Split actualizado.")
    st.divider()
    st.caption("Categorías: editá libremente la pestaña `categories` del Sheet (una por fila).")
