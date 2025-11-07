if page == "➕ Registrar":
    st.subheader("Nuevo movimiento")

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

    # --- Pagador y destinatario ---
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

    # ---- Distribución del gasto ----
    st.markdown("### 💰 Distribución del gasto")

    # Base dinámica según quién pagó
    is_juan_payer = paid_by == "Juan"

    col1, col2, col3 = st.columns([3, 1, 2])

    # Slider principal
    with col1:
        base_val = int(split_juan * 100) if is_juan_payer else int((1 - split_juan) * 100)
        perc_value = st.slider(
            f"{'Juan' if is_juan_payer else 'Mailu'} (%)",
            min_value=0,
            max_value=100,
            value=base_val,
            step=1,
            key="split_slider",
            label_visibility="collapsed",
        )

    # Input manual sincronizado
    with col2:
        perc_input = st.number_input(
            "Editar %",
            min_value=0,
            max_value=100,
            value=perc_value,
            step=1,
            key="split_input",
            label_visibility="collapsed",
        )
        if perc_input != perc_value:
            perc_value = perc_input

    # Cálculo dinámico según quién pagó
    if is_juan_payer:
        perc_juan = perc_value
        perc_mailu = 100 - perc_juan
    else:
        perc_mailu = perc_value
        perc_juan = 100 - perc_mailu

    # Mostrar ambos valores
    with col3:
        st.markdown(
            f"""
            <div style='text-align:left; line-height:1.4'>
                <b>Juan:</b> {perc_juan}%<br>
                <b>Mailu:</b> {perc_mailu}%
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ---- Tipo, categoría, monto y notas ----
    st.markdown("### 📂 Tipo y categoría")

    mtype = st.selectbox(
        "Tipo",
        ["gasto", "ingreso"],
        index=0,
        key="mtype_select"
    )

    cat = st.selectbox(
        "Categoría",
        categories,  # usa tu lista dinámica desde Google Sheets
        index=0,
        key="cat_select"
    )

    amount = st.number_input("Monto", min_value=0.0, step=0.01, format="%.2f")
    notes = st.text_area("Notas")

    # ---- Guardar movimiento ----
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
            "amount_mailu": amount_mailu,
        }

        tx_ws.append_row(list(row.values()))
        st.success("Movimiento registrado ✅")

    st.divider()
    st.caption("Tip: Podés agregar o quitar categorías desde la pestaña `categories` en el Sheet.")
