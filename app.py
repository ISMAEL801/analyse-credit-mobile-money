import hashlib
import html
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from analyzer import (analyze, compute_trend, detect_client, extract_document_text, monthly_entries,
                      parse_transactions, rise_driven_by_atypical)
from db import history_csv, load_history, save_analysis
from ui import (ACCENT, ALERT, DISCLAIMER, MUTED, SIGNAL_COLORS, SIGNAL_TEXT, SUSPECT_BG, apply_style, esc,
                fmt_fcfa, login_card, section, user_header)

load_dotenv()
st.set_page_config(page_title="Analyse Mobile Money", page_icon="📊", layout="wide")

CARD_H = 380  # hauteur commune des 3 grandes cartes
CHART_H = 285
KPI_H = 245  # hauteur commune des 3 petites cartes (la carte Tendance contient le mini-graphique)
# Infobulle : date en titre + montant, et la raison uniquement pour les transactions signalées.
# Via la zone de détection du point le plus proche, la ligne de données est dans datum.datum.
TOOLTIP = {"expr": "datum.datum ? datum.datum.tip : datum.tip"}
TIP_EXPR = ("datum.raison ? {title: datum.jour, 'Montant': datum.montant_txt, 'Signalement': datum.raison}"
            " : {title: datum.jour, 'Montant': datum.montant_txt}")

apply_style()


def flag_suspects(df, suspects):
    """Ajoute la colonne 'Analyse IA' en rapprochant les transactions signalées par l'IA (date + montant)."""
    df = df.assign(**{"Analyse IA": ""})
    for s in suspects:
        if not isinstance(s, dict):
            continue
        try:
            amount = int(float(s.get("montant")))
        except (TypeError, ValueError):
            continue
        date = str(s.get("date", "")).strip()
        mask = (df["Montant"] == amount) & df["Date (brute)"].str.startswith(date[:10])
        df.loc[mask, "Analyse IA"] = "🚨 " + str(s.get("raison", "signalée"))
    return df


MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
TREND_COLORS = {"croissante": "#1e8e3e", "décroissante": "#e37400", "stable": MUTED}  # vert / orange / neutre


def trend_minichart(monthly, trend):
    """Mini-graphique SVG : entrées par mois (barres) + flèche dans le sens de la tendance calculée par Python."""
    if monthly.empty:
        return ""
    color = TREND_COLORS.get(trend, MUTED)
    W, H, top, label_h = 360, 78, 8, 16
    plot_h = H - top - label_h
    slot = W / len(monthly)
    bar_w = min(slot * 0.6, 56)
    top_value = max(int(monthly["total"].max()), 1)
    parts = []
    for i, m in enumerate(monthly.itertuples()):
        h = plot_h * m.total / top_value
        x, y = i * slot + (slot - bar_w) / 2, top + plot_h - h
        partial = m.jours_couverts < m.jours_mois
        tip = f"{MONTHS_FR[m.mois.month - 1]} {m.mois.year} : {fmt_fcfa(m.total)} reçus" + (
            f" (mois partiel : {m.jours_couverts} jours sur {m.jours_mois})" if partial else "")
        parts.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{max(h, 1.5):.1f}' rx='3' fill='{color}'"
                     f" fill-opacity='{0.3 if partial else 0.75}'><title>{esc(tip)}</title></rect>")
        parts.append(f"<text x='{x + bar_w / 2:.1f}' y='{H - 3}' text-anchor='middle' font-size='10' fill='{MUTED}'>"
                     f"{MONTHS_FR[m.mois.month - 1]}</text>")
    # Flèche superposée : sens donné par la tendance calculée (pas de flèche si elle n'a pas pu être déterminée)
    hi, lo, mid = top + 6, top + plot_h - 6, top + plot_h / 2
    arrow = {"croissante": (lo, hi), "décroissante": (hi, lo), "stable": (mid, mid)}.get(trend)
    if arrow:
        x1, x2 = 10, W - 14
        line = f"x1='{x1}' y1='{arrow[0]:.1f}' x2='{x2}' y2='{arrow[1]:.1f}' stroke-linecap='round'"
        parts.append(
            f"<defs><marker id='trend-head' viewBox='0 0 10 10' refX='6' refY='5' markerWidth='4.5' markerHeight='4.5'"
            f" orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='{color}'/></marker></defs>"
            f"<line {line} stroke='white' stroke-opacity='.9' stroke-width='7'/>"
            f"<line {line} stroke='{color}' stroke-width='3' marker-end='url(#trend-head)'/>")
    return (f"<svg viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='Entrées par mois, tendance {esc(trend)}'"
            f" style='display:block;margin-top:.5rem'>{''.join(parts)}</svg>")


def chart_data(df):
    data = pd.DataFrame({
        "date": df["Date"],
        "montant": df["Montant"],
        "jour": df["Date"].dt.strftime("%d/%m"),
        "montant_txt": df["Montant"].map(fmt_fcfa),
        "raison": df["Analyse IA"].str.removeprefix("🚨 "),
    })
    # Coupe la courbe quand il n'y a aucune transaction pendant plus de 2 jours (pas de fausse continuité) :
    # une période d'activité = un segment, tracé séparément pour que la zone remplie ne traverse pas les interruptions.
    data["segment"] = (data["date"].diff() > pd.Timedelta(days=2)).cumsum()
    data["isole"] = data.groupby("segment")["date"].transform("size") == 1
    return data


def amounts_chart(df):
    data = chart_data(df)
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%d/%m", grid=False, domain=False, ticks=False, labelColor=MUTED))
    y = alt.Y("montant:Q", title=None, stack=None,
              axis=alt.Axis(labelExpr="datum.value == 0 ? '0' : format(datum.value, '~s')", domain=False, ticks=False, labelColor=MUTED, gridColor=MUTED, gridOpacity=0.18))
    base = alt.Chart(data).transform_calculate(tip=TIP_EXPR).encode(x=x, y=y)
    hover =alt.selection_point(nearest=True, on="pointerover", clear="pointerout", fields=["date"], empty=False)

    gradient = alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0, stops=[
        alt.GradientStop(color="rgba(42,111,219,0)", offset=0), alt.GradientStop(color="rgba(42,111,219,0.32)", offset=1)])
    area = base.mark_area(interpolate="monotone", color=gradient).encode(detail="segment:N")
    line = base.mark_line(interpolate="monotone", color=ACCENT, strokeWidth=2).encode(detail="segment:N")
    isolated = base.transform_filter(alt.datum.isole).mark_circle(size=36, color=ACCENT, opacity=1)  # jour isolé visible

    # Zone de détection invisible (point le plus proche de la souris) qui porte l'infobulle
    selectors = base.mark_point(opacity=0, size=60, tooltip=TOOLTIP).add_params(hover)
    # ligne verticale pointillée sur toute la hauteur (encodée sur x seulement)
    rule = alt.Chart(data).mark_rule(color=MUTED, strokeDash=[4, 4], strokeWidth=1.2).encode(x=x).transform_filter(hover)
    hover_point = base.mark_circle(size=90, color=ACCENT, stroke="white", strokeWidth=2, opacity=1).transform_filter(hover)
    diamonds = base.transform_filter(alt.datum.raison != "").mark_point(
        shape="diamond", filled=True, size=260, color=ALERT, stroke="white", strokeWidth=2, opacity=1, tooltip=TOOLTIP)

    return (alt.layer(area, line, isolated, selectors, rule, hover_point, diamonds)
            .properties(height=CHART_H).configure_view(stroke=None))


# --- Onglet 1 : nouvelle analyse ---
def read_document(uploaded, pasted):
    """Texte du document (le fichier importé est prioritaire sur le texte collé) et une signature du document,
    pour ne relancer l'extraction et la détection du client que lorsque le document change."""
    if uploaded is not None:
        data = uploaded.getvalue()
        sig = ("fichier", uploaded.name, hashlib.md5(data).hexdigest())
        if sig == st.session_state.get("_doc_sig"):
            return st.session_state["_doc_text"], sig
        return extract_document_text(uploaded.name, data), sig
    if pasted.strip():
        return pasted, ("texte", hashlib.md5(pasted.encode("utf-8")).hexdigest())
    return "", None


def page_analyse(agent):
    tab_file, tab_text = st.tabs(["📄 Importer un fichier (PDF / TXT)", "📋 Coller du texte"])
    with tab_file:
        uploaded = st.file_uploader("Relevé de transactions", type=["pdf", "txt", "csv"])
    with tab_text:
        pasted = st.text_area("Historique (format : Date | Montant | Type)", height=200)

    # Nouveau document : on cherche le client dans son en-tête et on pré-remplit le champ (toujours modifiable).
    # Tant que le document ne change pas, les corrections de l'analyste sont conservées.
    text, sig = read_document(uploaded, pasted)
    if sig != st.session_state.get("_doc_sig"):
        st.session_state.update(_doc_sig=sig, _doc_text=text)
        if sig is not None:
            detected = detect_client(text)
            st.session_state.update(client=detected or "", client_auto=detected is not None)
    auto = sig is not None and st.session_state.get("client_auto")
    client = st.text_input(
        "Nom ou référence du client *", key="client",
        placeholder="Non détecté automatiquement — à saisir" if sig is not None and not auto
        else "ex. Boutique Awa – Adjamé, ou n° de dossier",
    )
    if sig is not None:
        st.caption("🔎 Pré-rempli à partir de l'en-tête du document — vous pouvez le corriger avant de lancer l'analyse."
                   if auto else "⚠️ Non détecté automatiquement — saisissez le nom ou la référence du client.")

    if st.button("Analyser", type="primary"):
        if not client.strip():
            st.error("Renseignez le nom ou la référence du client avant de lancer l'analyse.")
            return
        with st.status("Analyse en cours…", expanded=True) as status:
            st.write("📄 Lecture du document…")
            if not text.strip():
                status.update(label="Aucun texte à analyser", state="error")
                st.error("Aucun texte à analyser : importe un fichier ou colle un historique.")
                return
            st.write("🧮 Lecture des transactions et calcul des indicateurs…")
            df = parse_transactions(text)
            st.write(f"🤖 Analyse par l'IA de {len(df)} transactions…")
            try:
                result = analyze(text, df)
            except Exception as e:
                status.update(label="Échec de l'analyse IA", state="error")
                st.error(f"Erreur lors de l'analyse IA : {e}")
                return
            st.write("💾 Enregistrement dans le journal d'audit…")
            try:
                analysis_id = save_analysis(agent, client.strip(), result, text)
            except Exception as e:
                status.update(label="Analyse non enregistrée", state="error")
                st.error(f"L'analyse a réussi mais n'a pas pu être enregistrée dans l'historique : {e}")
                return
            status.update(label=f"Analyse terminée et enregistrée ({analysis_id})", state="complete", expanded=False)
        st.session_state.update(text=text, df=df, result=result, analysis_id=analysis_id, client_ref=client.strip())

    if "result" in st.session_state:
        show_results(agent)


def show_results(agent):
    text, df, result = st.session_state.text, st.session_state.df, st.session_state.result
    df = flag_suspects(df, result["transactions_suspectes"])

    signal = result["signal_global"]
    color = SIGNAL_COLORS.get(signal, "#5f6368")
    icon, message = SIGNAL_TEXT.get(signal, ("❔", ""))
    anomalies = result["anomalies_detectees"]

    st.write("")
    # Ligne 1 : 3 grandes cartes (elles s'empilent automatiquement sur écran étroit)
    c1, c2, c3 = st.columns([1, 1, 2], gap="medium")
    with c1.container(border=True, height=CARD_H):
        st.markdown(
            f"<div class='signal-card'><div class='card-label'>Signal global de l'analyse</div>"
            f"<div class='signal-icon'>{icon}</div>"
            f"<div class='badge' style='background:{color}1f;color:{color};border:2px solid {color}'>{esc(signal.upper())}</div>"
            f"<div>{message}</div></div>",
            unsafe_allow_html=True,
        )
    with c2.container(border=True, height=CARD_H):
        st.markdown(
            f"<div class='card-label'>Résumé des transactions</div>"
            f"<div class='big-number'>{esc(result['nombre_transactions'])}"
            f"<div class='kpi-detail' style='font-weight:400'>transactions analysées</div></div>"
            f"<div class='stat-row'><span>Montant moyen</span><span>{esc(fmt_fcfa(result['montant_moyen']))}</span></div>"
            f"<div class='stat-row'><span>Période couverte</span><span>{esc(result['periode_couverte'])}</span></div>",
            unsafe_allow_html=True,
        )
    with c3.container(border=True, height=CARD_H):
        st.markdown(
            "<div class='chart-title'>📈 Montants des transactions dans le temps</div>"
            f"<div class='legend'><i style='width:14px;height:3px;background:{ACCENT};margin-left:0'></i>Transactions"
            f"<i style='width:9px;height:9px;background:{ALERT};transform:rotate(45deg)'></i>Signalée par l'IA</div>",
            unsafe_allow_html=True,
        )
        if df.empty:
            st.caption("Aucune transaction lue automatiquement : graphique indisponible.")
        else:
            st.altair_chart(amounts_chart(df), width="stretch")

    # Ligne 2 : petites cartes
    days = df["Date"].dt.normalize().nunique() if not df.empty else 0
    span = (df["Date"].max() - df["Date"].min()).days + 1 if not df.empty else 0
    trend = result["tendance"]
    trend_icon = {"croissante": "↗", "décroissante": "↘", "stable": "→"}.get(trend, "")
    _, change = compute_trend(df) if not df.empty else (None, None)
    trend_detail = "évolution des entrées par mois" + (f" ({change:+.0%})" if change is not None else "")
    anomaly_color = ALERT if anomalies else SIGNAL_COLORS["favorable"]
    k1, k2, k3 = st.columns(3, gap="medium")
    for col, label, value, detail, value_color, extra in [
        (k1, "Régularité", result["regularite"], f"{days} jours actifs sur {span}" if span else "", None, ""),
        (k2, "Anomalies détectées", f"{'🚨' if anomalies else '✅'} {len(anomalies)}",
         "à examiner ci-dessous" if anomalies else "aucune anomalie relevée", anomaly_color, ""),
        (k3, "Tendance", f"{trend_icon} {trend}".strip(), trend_detail, TREND_COLORS.get(trend),
         (f"<div class='kpi-warning' title='Sans les entrées atypiques (voir anomalies), les entrées ne sont pas en hausse'>"
          f"⚠️ Hausse portée par des montants atypiques</div>"
          if rise_driven_by_atypical(df) else "") + trend_minichart(monthly_entries(df), trend)),
    ]:
        with col.container(border=True, height=KPI_H):
            style = f" style='color:{value_color}'" if value_color else ""
            st.markdown(
                f"<div class='card-label'>{label}</div><div class='kpi-value'{style}>{esc(value)}</div>"
                f"<div class='kpi-detail'>{esc(detail)}</div>{extra}",
                unsafe_allow_html=True,
            )

    # Identifiant d'audit de l'analyse
    st.markdown(
        f"<div class='audit-id'>🧾 Analyse enregistrée sous l'identifiant <b>{esc(st.session_state.analysis_id)}</b>"
        f"<span>Client : {esc(st.session_state.client_ref)} · Analyste : {esc(agent['nom'])}</span></div>",
        unsafe_allow_html=True,
    )

    # Contrôle de cohérence entre l'IA et la lecture automatique
    if not df.empty and isinstance(result["nombre_transactions"], int) and result["nombre_transactions"] != len(df):
        st.warning(f"Contrôle : l'IA indique {result['nombre_transactions']} transactions, la lecture automatique en compte {len(df)}.")

    section("📝 Résumé de l'analyse")
    st.markdown(f"<div class='summary'>{html.escape(result['resume_texte'])}</div>", unsafe_allow_html=True)

    section(f"🚨 Anomalies détectées ({len(anomalies)})" if anomalies else "Anomalies détectées")
    if anomalies:
        st.markdown("".join(f"<div class='anomaly'>🚨 {html.escape(str(a))}</div>" for a in anomalies), unsafe_allow_html=True)
    else:
        st.success("✅ Aucune anomalie détectée.")

    section("📋 Détail des transactions")
    if df.empty:
        st.caption("Aucune ligne 'Date | Montant | Type' n'a pu être lue automatiquement.")
    else:
        table = df[["Date (brute)", "Montant", "Type", "Analyse IA"]].rename(columns={"Date (brute)": "Date"})
        n_flagged = (table["Analyse IA"] != "").sum()
        if n_flagged and st.toggle(f"Afficher uniquement les {n_flagged} transactions signalées par l'IA"):
            table = table[table["Analyse IA"] != ""]
        st.dataframe(
            table.style.apply(lambda r: [f"background-color: {SUSPECT_BG}" if r["Analyse IA"] else ""] * len(r), axis=1)
            .format({"Montant": fmt_fcfa}),
            width="stretch",
            hide_index=True,
        )
    # Les « Détails techniques » (JSON brut de l'IA, texte extrait) sont réservés à l'espace admin (/Admin)


# --- Onglet 2 : historique personnel de l'analyste ---
def page_historique(agent):
    history = load_history(agent["login"])
    if history.empty:
        st.info("Vous n'avez encore enregistré aucune analyse. Elles apparaîtront ici dès que vous en lancerez une.")
        return
    info, button = st.columns([3, 1], vertical_alignment="center")
    info.markdown(f"**{len(history)} analyse(s) enregistrée(s) par vous**, de la plus récente à la plus ancienne.")
    button.download_button(
        "⬇️ Télécharger mon historique (CSV)", data=history_csv(agent["login"]), mime="text/csv", width="stretch",
        file_name=f"historique_{agent['login']}_{datetime.now():%Y-%m-%d}.csv",
    )
    st.dataframe(
        history.style.map(lambda s: f"color: {SIGNAL_COLORS.get(s, 'inherit')}; font-weight: 700", subset=["Signal global"]),
        width="stretch",
        hide_index=True,
        column_config={
            "Anomalies détectées": st.column_config.TextColumn(width="large"),
            "Résumé de l'IA": st.column_config.TextColumn(width="large"),
        },
    )


def logout():
    """Déconnexion de l'espace analyste (la session de l'espace admin, si ouverte, est conservée)."""
    for key in [k for k in st.session_state if not k.startswith("admin")]:
        del st.session_state[key]


# --- Page ---
if "agent" not in st.session_state:
    agent = login_card("connexion", "📊", "Analyse de compte marchand Mobile Money", "Espace réservé aux analystes crédit")
    if agent:
        st.session_state.agent = agent
        st.rerun()
    st.stop()

agent = st.session_state.agent
user_header("📊 Analyse de compte marchand Mobile Money", agent, logout)
st.caption("Wave Business · Orange Money Business · MTN MoMo Business — analyse assistée par IA")
st.info(DISCLAIMER)

tab_analyse, tab_historique = st.tabs(["🔍 Nouvelle analyse", "🗂️ Mon historique"])
with tab_analyse:
    page_analyse(agent)
with tab_historique:
    page_historique(agent)

st.info(DISCLAIMER)
