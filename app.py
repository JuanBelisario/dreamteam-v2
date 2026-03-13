# Dreamteam v3 – Mejoras: ingresos, gastos personales, historial, validaciones, reset automático
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

NOMBRES = ["Juan", "Mailu"]  # ← Cambiá estos nombres si hacés fork para otra pareja

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
        sh.add_worksheet("transactions", rows=1000, cols=14)
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
    return cats if cats else ["Ingresos", "Supermercado", "Comidas", "Transporte", "Servicios"]

@st.cache_data(ttl=60)
def read_transactions_data(tx_values):
    expected_cols = [
        "timestamp", "entry_user", "paid_by", "paid_for", "type", "scope",
        "category", "currency", "amount", "notes",
        "split_juan", "split_mailu", "amount_juan", "amount_mailu"
    ]
    if not tx_values or len(tx_values) < 2:
        return pd.DataFrame(columns=expected_cols)

    header = tx_values[0]

    # Retrocompatibilidad: agregar columnas nuevas si no existen
    for col in ["currency", "scope"]:
        if col not in header:
            header = header.copy()
            if col == "currency" and "category" in header:
                header.insert(header.index("category") + 1, col)
            elif col == "scope" and "type" in header:
                header.insert(header.index("type") + 1, col)
            else:
                header.append(col)

    fixed_rows = []
    for row in tx_values[1:]:
        r = row.copy()
        if len(r) < len(header):
            r.extend([""] * (len(header) - len(r)))
        elif len(r) > len(header):
            r = r[:len(header)]
        fixed_rows.append(r)

    df = pd.DataFrame(fixed_rows, columns=header)

    for col in ["amount", "amount_juan", "amount_mailu", "split_juan", "split_mailu"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if "currency" not in df.columns:
        df["currency"] = "ARS"
    else:
        df["currency"] = df["currency"].replace("", "ARS").fillna("ARS")

    # scope: "compartido" por default para registros viejos
    if "scope" not in df.columns:
        df["scope"] = "compartido"
    else:
        df["scope"] = df["scope"].replace("", "compartido").fillna("compartido")

    return df

def append_transaction(tx_ws, row_dict):
    headers = _retry(lambda: tx_ws.row_values(1))
    # Retrocompatibilidad: agregar currency y scope al header si no están
    changed = False
    for col, after in [("currency", "category"), ("scope", "type")]:
        if col not in headers:
            if after in headers:
                headers.insert(headers.index(after) + 1, col)
            else:
                headers.append(col)
            changed = True
    if changed:
        _retry(lambda: tx_ws.update("A1", [headers]))
    ordered = [row_dict.get(h, "") for h in headers]
    _retry(lambda: tx_ws.append_row(ordered, value_input_option="USER_ENTERED"))

# ---------- LÓGICA DE DEUDA ----------
def compute_debt(df, default_split_juan=0.6, default_split_mailu=0.4):
    # Solo considera gastos compartidos
    g = df[
        (df["type"].str.lower() == "gasto") &
        (df["scope"].str.lower() == "compartido")
    ].copy()
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

def compute_debt_for_currency(df, currency, default_split_juan=0.6, default_split_mailu=0.4):
    if "currency" not in df.columns:
        if currency == "ARS":
            return compute_debt(df, default_split_juan, default_split_mailu)
        return 0.0, 0.0, 0.0
    df_cur = df[df["currency"] == currency]
    if df_cur.empty:
        return 0.0, 0.0, 0.0
    return compute_debt(df_cur, default_split_juan, default_split_mailu)

# ---------- BOOTSTRAP ----------
if "sheets" not in st.session_state:
    st.session_state["sheets"] = open_sheet()
sheets = st.session_state["sheets"]

tx_ws = sheets["tx"]
cfg_ws = sheets["cfg"]
cat_ws = sheets["cat"]

TX_HEADERS = [
    "timestamp", "entry_user", "paid_by", "paid_for", "type", "scope",
    "category", "currency", "amount", "notes",
    "split_juan", "split_mailu", "amount_juan", "amount_mailu"
]
if "tx_headers_ok" not in st.session_state:
    ensure_headers(tx_ws, TX_HEADERS)
    st.session_state["tx_headers_ok"] = True

# ---- Cargar datos ----
@st.cache_data(ttl=300)
def load_cfg(_ws):
    return _ws.get_all_values()

@st.cache_data(ttl=60)
def load_tx(_ws):
    return _ws.get_all_values()

cfg_values = load_cfg(cfg_ws)
cat_values = load_cfg(cat_ws)
tx_values = load_tx(tx_ws)

split_juan, split_mailu = read_config_data(cfg_values)
categories = read_categories_data(cat_values)
df = read_transactions_data(tx_values)

# ---------- UI: TÍTULO Y BALANCES ----------
st.title("💸 Dreamteam v3")

# Balances por moneda
col_bal_ars, col_bal_usd = st.columns(2)

juan_net_ars, mailu_net_ars, mailu_owes_juan_ars = compute_debt_for_currency(df, "ARS", split_juan, split_mailu)
juan_net_usd, mailu_net_usd, mailu_owes_juan_usd = compute_debt_for_currency(df, "USD", split_juan, split_mailu)

with col_bal_ars:
    st.markdown("**Balance ARS (compartido)**")
    if mailu_owes_juan_ars > 0:
        st.success(f"💚 Mailu → Juan: **${mailu_owes_juan_ars:,.0f}**")
    elif juan_net_ars < 0:
        st.success(f"💚 Juan → Mailu: **${abs(juan_net_ars):,.0f}**")
    else:
        st.info("⚖️ A mano en ARS")

with col_bal_usd:
    st.markdown("**Balance USD (compartido)**")
    if mailu_owes_juan_usd > 0:
        st.success(f"💚 Mailu → Juan: **${mailu_owes_juan_usd:,.0f}**")
    elif juan_net_usd < 0:
        st.success(f"💚 Juan → Mailu: **${abs(juan_net_usd):,.0f}**")
    elif df[df["currency"] == "USD"].empty:
        st.info("💸 Sin movimientos USD")
    else:
        st.info("⚖️ A mano en USD")

# Mostrar último movimiento registrado (si existe)
if "ultimo_registro" in st.session_state and st.session_state["ultimo_registro"]:
    u = st.session_state["ultimo_registro"]
    st.caption(
        f"✅ Último registro: **{u['category']}** — {u['currency']} ${u['amount']:,.0f} "
        f"({u['type'].capitalize()}, {u['scope']}) — {u['timestamp']}"
    )

st.divider()

# ---------- FORMULARIO ----------
st.subheader("Nuevo movimiento")

# --- Tipo de movimiento y scope ---
col_tipo, col_scope = st.columns(2)
with col_tipo:
    mtype = st.radio(
        "Tipo",
        ["Gasto", "Ingreso"],
        horizontal=True,
        key="mtype_radio",
    )
with col_scope:
    scope = st.radio(
        "Es...",
        ["Compartido", f"Personal {NOMBRES[0]}", f"Personal {NOMBRES[1]}"],
        horizontal=True,
        key="scope_radio",
        help="Los gastos/ingresos personales se guardan pero NO afectan el balance entre ustedes.",
    )

# Si es personal, paid_for se deriva automáticamente del scope
if scope == "Compartido":
    paid_for = "Ambos"
elif scope == f"Personal {NOMBRES[0]}":
    paid_for = NOMBRES[0]
else:
    paid_for = NOMBRES[1]

# --- Fecha y moneda ---
colA, colB = st.columns(2)
with colA:
    dt = st.date_input("Fecha", pd.Timestamp.now().date())
with colB:
    currency = st.radio(
        "Moneda",
        ["ARS", "USD"],
        horizontal=True,
        index=0,
        key="currency_radio",
    )

# --- Quién pagó (solo relevante si es compartido) ---
if scope == "Compartido":
    st.write("**¿Quién pagó?**")
    paid_by = st.radio(
        "¿Quién pagó?",
        NOMBRES,
        horizontal=True,
        key="paid_by_radio",
        label_visibility="collapsed",
    )
else:
    # Si es personal, quien pagó es la misma persona del scope
    paid_by = paid_for
    st.caption(f"👤 Movimiento personal de **{paid_by}** — no afecta el balance compartido.")

# --- Split (solo si es gasto compartido) ---
if scope == "Compartido" and mtype == "Gasto":
    st.markdown("### 💰 Distribución del gasto")
    is_juan_payer = (paid_by == NOMBRES[0])
    base_val = int(split_juan * 100) if is_juan_payer else int((1 - split_juan) * 100)

    sv_key = "split_value"
    last_key = "last_payer"

    if sv_key not in st.session_state:
        st.session_state[sv_key] = base_val
    if last_key not in st.session_state:
        st.session_state[last_key] = paid_by
    if st.session_state[last_key] != paid_by:
        st.session_state[last_key] = paid_by
        st.session_state[sv_key] = base_val

    col1, col2, col3 = st.columns([3, 1, 2])
    payer_label = NOMBRES[0] if is_juan_payer else NOMBRES[1]
    with col1:
        slider_val = st.slider(
            f"{payer_label} (%)", 0, 100,
            st.session_state[sv_key], 1,
            key=f"split_slider_{'J' if is_juan_payer else 'M'}",
            label_visibility="collapsed",
        )
    with col2:
        input_val = st.number_input(
            "Editar %", 0, 100,
            st.session_state[sv_key], 1,
            key=f"split_input_{'J' if is_juan_payer else 'M'}",
            label_visibility="collapsed",
        )

    # Sincronización slider ↔ input (el último en cambiar gana)
    new_val = input_val if input_val != st.session_state[sv_key] else (
        slider_val if slider_val != st.session_state[sv_key] else st.session_state[sv_key]
    )
    st.session_state[sv_key] = new_val
    perc_value = new_val

    perc_juan = perc_value if is_juan_payer else 100 - perc_value
    perc_mailu = 100 - perc_juan

    with col3:
        st.markdown(
            f"<div style='text-align:left; line-height:1.8; padding-top:4px'>"
            f"<b>{NOMBRES[0]}:</b> {perc_juan}%<br>"
            f"<b>{NOMBRES[1]}:</b> {perc_mailu}%"
            f"</div>",
            unsafe_allow_html=True,
        )
else:
    perc_juan = 100 if paid_by == NOMBRES[0] else 0
    perc_mailu = 100 - perc_juan

# --- Categoría, monto y notas ---
st.markdown("### 📂 Detalle")

cat = st.radio(
    "Categoría",
    categories,
    index=0,
    key="cat_radio",
    horizontal=True,
)

# Reset automático del monto después de guardar
amount_default = 0 if st.session_state.get("reset_amount", False) else st.session_state.get("last_amount", 0)
if st.session_state.get("reset_amount", False):
    st.session_state["reset_amount"] = False

amount = st.number_input(
    f"Monto ({currency})",
    min_value=0,
    value=amount_default,
    step=1,
    format="%d",
    key="amount_input",
)

notes = st.text_area("Notas (opcional)", key="notes_input")

# --- Botón guardar ---
if st.button("Guardar ✅", use_container_width=True):
    if amount <= 0:
        st.warning("⚠️ El monto debe ser mayor a 0 para guardar.")
    else:
        ts = datetime.combine(dt, datetime.now().time())  # hora real
        amount_juan = float(amount) * (perc_juan / 100)
        amount_mailu = float(amount) * (perc_mailu / 100)

        row = {
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "entry_user": paid_by,
            "paid_by": paid_by,
            "paid_for": paid_for,
            "type": mtype.lower(),
            "scope": scope.lower(),
            "category": cat,
            "currency": currency,
            "amount": float(amount),
            "notes": notes,
            "split_juan": perc_juan / 100,
            "split_mailu": perc_mailu / 100,
            "amount_juan": amount_juan,
            "amount_mailu": amount_mailu,
        }
        append_transaction(tx_ws, row)

        # Guardar info del último registro para mostrar arriba
        st.session_state["ultimo_registro"] = {
            "category": cat,
            "currency": currency,
            "amount": float(amount),
            "type": mtype,
            "scope": scope,
            "timestamp": ts.strftime("%d/%m %H:%M"),
        }

        # Reset automático del monto
        st.session_state["reset_amount"] = True

        # Invalidar caché de transacciones
        load_tx.clear()

        st.success("Movimiento registrado ✅")
        st.rerun()

# ---------- HISTORIAL ----------
st.divider()
with st.expander("📋 Historial de movimientos", expanded=False):
    if df.empty:
        st.info("Todavía no hay movimientos registrados.")
    else:
        # Filtros
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            meses_disp = sorted(
                df["timestamp"].dropna().dt.to_period("M").unique(),
                reverse=True
            )
            mes_labels = [str(m) for m in meses_disp]
            mes_sel = st.selectbox("Mes", ["Todos"] + mes_labels, key="hist_mes")
        with col_f2:
            scope_sel = st.selectbox("Scope", ["Todos", "Compartido", "Personal"], key="hist_scope")
        with col_f3:
            tipo_sel = st.selectbox("Tipo", ["Todos", "Gasto", "Ingreso"], key="hist_tipo")

        df_hist = df.copy()
        if mes_sel != "Todos":
            df_hist = df_hist[df_hist["timestamp"].dt.to_period("M").astype(str) == mes_sel]
        if scope_sel != "Todos":
            df_hist = df_hist[df_hist["scope"].str.lower().str.contains(scope_sel.lower())]
        if tipo_sel != "Todos":
            df_hist = df_hist[df_hist["type"].str.lower() == tipo_sel.lower()]

        df_hist = df_hist.sort_values("timestamp", ascending=False).head(50)

        if df_hist.empty:
            st.info("Sin movimientos con ese filtro.")
        else:
            # Resumen rápido del período filtrado
            gastos_comp = df_hist[
                (df_hist["type"].str.lower() == "gasto") &
                (df_hist["scope"].str.lower() == "compartido")
            ]
            if not gastos_comp.empty:
                ars_tot = gastos_comp[gastos_comp["currency"] == "ARS"]["amount"].sum()
                usd_tot = gastos_comp[gastos_comp["currency"] == "USD"]["amount"].sum()
                resumen = []
                if ars_tot > 0:
                    resumen.append(f"**ARS ${ars_tot:,.0f}**")
                if usd_tot > 0:
                    resumen.append(f"**USD ${usd_tot:,.0f}**")
                if resumen:
                    st.caption(f"Gastos compartidos en período: {' | '.join(resumen)}")

                # Top categorías
                top_cat = (
                    gastos_comp[gastos_comp["currency"] == "ARS"]
                    .groupby("category")["amount"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(3)
                )
                if not top_cat.empty:
                    cats_str = " · ".join([f"{c}: ${v:,.0f}" for c, v in top_cat.items()])
                    st.caption(f"Top categorías ARS: {cats_str}")

            # Tabla
            cols_mostrar = ["timestamp", "type", "scope", "paid_by", "category", "currency", "amount", "notes"]
            cols_existentes = [c for c in cols_mostrar if c in df_hist.columns]
            st.dataframe(
                df_hist[cols_existentes].rename(columns={
                    "timestamp": "Fecha",
                    "type": "Tipo",
                    "scope": "Scope",
                    "paid_by": "Pagó",
                    "category": "Categoría",
                    "currency": "Moneda",
                    "amount": "Monto",
                    "notes": "Notas",
                }),
                use_container_width=True,
                hide_index=True,
            )

st.caption("Podés editar categorías y splits globales desde el Sheet directamente.")
