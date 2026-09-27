"""Espace admin (/Admin) : supervision de toutes les analyses, réservé au rôle « admin »."""
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from analyzer import local_facts, parse_transactions
from db import history_csv, load_analysis, load_history
from ui import (ACCENT, MUTED, ROLE_ADMIN, SIGNAL_COLORS, SIGNAL_TEXT, apply_style, esc, login_card, section,
                user_header)

load_dotenv()
st.set_page_config(page_title="Espace admin", page_icon="🛠️", layout="wide")
apply_style()

SIGNALS = ["favorable", "à vérifier", "suspect"]
KPI_H = 150
CHARTS_H = 330
TEXT = "#374151"


def logout():
    """Déconnexion de l'espace admin uniquement (la session analyste éventuelle est conservée)."""
    for key in [k for k in st.session_state if k.startswith("admin")]:
        del st.session_state[key]


def agents_chart(per_agent):
    data = per_agent.rename_axis("agent").reset_index(name="analyses")
    base = alt.Chart(data).encode(
        y=alt.Y("agent:N", sort="-x", title=None,
                axis=alt.Axis(domain=False, ticks=False, labelColor=TEXT, labelFontSize=13, labelLimit=180, labelPadding=8)),
        x=alt.X("analyses:Q", title=None, scale=alt.Scale(domainMax=int(data["analyses"].max()) + 1),
                axis=alt.Axis(tickMinStep=1, format="d", domain=False, ticks=False, labelColor=MUTED,
                              gridColor=MUTED, gridOpacity=0.18)),
    )
    bars = base.mark_bar(color=ACCENT, cornerRadiusEnd=4, height={"band": 0.6}).encode(
        tooltip=[alt.Tooltip("agent:N", title="Analyste crédit"), alt.Tooltip("analyses:Q", title="Analyses")])
    labels = base.mark_text(align="left", dx=6, fontWeight="bold", fontSize=13, color=TEXT).encode(text="analyses:Q")
    return (bars + labels).properties(height=max(110, 46 * len(data))).configure_view(stroke=None)


def signals_donut(counts, total):
    data = counts.rename_axis("signal").reset_index(name="n")
    data = data[data["n"] > 0].assign(part=lambda d: d["n"] / total, ordre=lambda d: d["signal"].map(SIGNALS.index))
    arc = alt.Chart(data).mark_arc(innerRadius=68, outerRadius=108, stroke="white", strokeWidth=2).encode(
        theta=alt.Theta("n:Q", stack=True),
        order=alt.Order("ordre:Q"),
        color=alt.Color("signal:N", legend=None,
                        scale=alt.Scale(domain=SIGNALS, range=[SIGNAL_COLORS[s] for s in SIGNALS])),
        tooltip=[alt.Tooltip("signal:N", title="Signal"), alt.Tooltip("n:Q", title="Analyses"),
                 alt.Tooltip("part:Q", title="Part", format=".0%")],
    )
    center = alt.Chart(pd.DataFrame({"t": [str(total)]})).mark_text(
        fontSize=30, fontWeight="bold", color=TEXT, dy=-6).encode(text="t:N")
    caption = alt.Chart(pd.DataFrame({"t": ["analyses"]})).mark_text(
        fontSize=12, color=MUTED, dy=18).encode(text="t:N")
    return alt.layer(arc, center, caption).properties(height=240).configure_view(stroke=None)


# --- Connexion (indépendante de la page principale : seul le rôle admin est accepté) ---
if "admin_user" not in st.session_state:
    user = login_card("connexion_admin", "🛠️", "Espace admin",
                      "Supervision de l'outil — accès réservé aux administrateurs", role_required=ROLE_ADMIN)
    if user:
        st.session_state.admin_user = user
        st.rerun()
    st.stop()

user = st.session_state.admin_user
user_header("🛠️ Espace admin — Supervision", user, logout)
st.caption("Vue d'ensemble de toutes les analyses enregistrées, tous analystes confondus")

history = load_history()
if history.empty:
    st.info("Aucune analyse enregistrée pour le moment.")
    st.stop()

total = len(history)
per_agent = history["Analyste"].value_counts()
signals = history["Signal global"].value_counts().reindex(SIGNALS, fill_value=0)

# --- Vue d'ensemble : chiffres clés ---
st.write("")
cols = st.columns(4, gap="medium")
with cols[0].container(border=True, height=KPI_H):
    st.markdown(
        f"<div class='card-label'>Analyses enregistrées</div><div class='kpi-value' style='font-size:2rem'>{total}</div>"
        f"<div class='kpi-detail'>par {len(per_agent)} analyste(s) crédit</div>", unsafe_allow_html=True)
for col, signal in zip(cols[1:], SIGNALS):
    count = int(signals[signal])
    with col.container(border=True, height=KPI_H):
        st.markdown(
            f"<div class='card-label'>{SIGNAL_TEXT[signal][0]} Signal « {signal} »</div>"
            f"<div class='kpi-value' style='font-size:2rem;color:{SIGNAL_COLORS[signal]}'>{count}</div>"
            f"<div class='kpi-detail'>{count / total:.0%} des analyses</div>", unsafe_allow_html=True)

# --- Graphiques ---
c1, c2 = st.columns([3, 2], gap="medium")
with c1.container(border=True, height=CHARTS_H):
    st.markdown("<div class='chart-title'>👥 Analyses par analyste crédit</div>", unsafe_allow_html=True)
    st.altair_chart(agents_chart(per_agent), width="stretch")
    st.markdown("".join(f"<div class='list-row'><span>{esc(agent)}</span><span>{n} analyse{'s' if n > 1 else ''}</span></div>"
                        for agent, n in per_agent.items()), unsafe_allow_html=True)
with c2.container(border=True, height=CHARTS_H):
    st.markdown("<div class='chart-title'>🎯 Répartition des signaux globaux</div>", unsafe_allow_html=True)
    donut, legend = st.columns([1.2, 1], vertical_alignment="center")
    donut.altair_chart(signals_donut(signals, total), width="stretch")
    legend.markdown("".join(
        f"<div class='list-row'><span><i class='dot' style='background:{SIGNAL_COLORS[s]}'></i>{s}</span>"
        f"<span>{int(signals[s])} · {signals[s] / total:.0%}</span></div>" for s in SIGNALS), unsafe_allow_html=True)

# --- Toutes les analyses ---
section("🗂️ Toutes les analyses")
info, button = st.columns([3, 1], vertical_alignment="center")
info.markdown(f"**{total} analyse(s)**, tous analystes confondus, de la plus récente à la plus ancienne.")
button.download_button("⬇️ Télécharger l'historique complet (CSV)", data=history_csv(), mime="text/csv",
                       width="stretch", file_name=f"historique_complet_{datetime.now():%Y-%m-%d}.csv")
st.dataframe(
    history.style.map(lambda s: f"color: {SIGNAL_COLORS.get(s, 'inherit')}; font-weight: 700", subset=["Signal global"]),
    width="stretch", hide_index=True,
    column_config={"Analyste": st.column_config.TextColumn("Analyste", help="Analyste crédit qui a lancé l'analyse"),
                   "Anomalies détectées": st.column_config.TextColumn(width="large"),
                   "Résumé de l'IA": st.column_config.TextColumn(width="large")},
)

# --- Détails techniques (réservés à l'espace admin) ---
with st.expander("🔧 Détails techniques d'une analyse"):
    labels = {row["ID"]: f"{row['ID']} — {row['Client']} ({row['Analyste']}, {row['Date']})" for _, row in history.iterrows()}
    chosen = st.selectbox("Analyse", list(labels), format_func=labels.get)
    details = load_analysis(chosen)
    st.markdown("**Réponse JSON complète de l'IA**")
    st.json(details["resultat"], expanded=False)
    if details["texte"]:
        st.markdown("**Indicateurs calculés par Python (envoyés à l'IA)**")
        st.code(local_facts(parse_transactions(details["texte"])), language=None)
        st.text_area("Texte extrait du document", details["texte"], height=220, disabled=True)
    else:
        st.caption("Texte du document non conservé pour cette analyse (enregistrée avant l'ajout de l'espace admin).")
