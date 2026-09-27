"""Éléments d'interface partagés entre la page principale et l'espace admin (styles, couleurs, connexion)."""
import html

import streamlit as st

from auth import authenticate

DISCLAIMER = (
    "⚖️ **Outil d'aide à l'analyse uniquement.** Cette analyse ne constitue pas une décision de crédit. "
    "La décision finale revient toujours à l'analyste crédit / à l'institution financière."
)
SIGNAL_COLORS = {"favorable": "#1e8e3e", "à vérifier": "#e37400", "suspect": "#d93025"}
SIGNAL_TEXT = {
    "favorable": ("✅", "Activité régulière, aucune anomalie notable relevée."),
    "à vérifier": ("⚠️", "Des irrégularités méritent un examen attentif par l'analyste crédit."),
    "suspect": ("🚨", "Des opérations inhabituelles nécessitent une vérification approfondie."),
}
SUSPECT_BG = "rgba(217, 48, 37, 0.15)"
ACCENT = "#2a6fdb"  # même bleu que l'encadré du résumé
ALERT = "#d93025"
MUTED = "#8a94a6"
ROLE_ADMIN = "admin"

CSS = """<style>
    .card-label {font-size:.78rem;opacity:.65;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
    .signal-card {display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;
                  gap:.7rem;height:300px}
    .signal-icon {font-size:3.4rem;line-height:1}
    .badge {display:inline-block;padding:.35rem 1.1rem;border-radius:999px;font-size:1.35rem;font-weight:800;
            letter-spacing:.04em}
    .big-number {font-size:2.6rem;font-weight:800;line-height:1.1;margin:.3rem 0 1.2rem}
    .stat-row {display:flex;justify-content:space-between;gap:1rem;padding:.65rem 0;
               border-top:1px solid rgba(128,128,128,.18)}
    .stat-row span:first-child {opacity:.65}
    .stat-row span:last-child {font-weight:700;text-align:right}
    .kpi-value {font-size:1.5rem;font-weight:800;margin-top:.3rem}
    .kpi-detail {font-size:.85rem;opacity:.65;margin-top:.1rem}
    .kpi-warning {font-size:.8rem;font-weight:600;color:#b45309;background:rgba(227,116,0,.1);
                  border-radius:6px;padding:.15rem .5rem;margin-top:.35rem;display:inline-block}
    .chart-title {font-weight:700;font-size:1rem;margin-bottom:.2rem}
    .legend {font-size:.8rem;opacity:.75;margin-bottom:.4rem}
    .legend i {display:inline-block;vertical-align:middle;margin:0 .35rem 0 .9rem}
    .section-title {font-size:1.2rem;font-weight:700;margin:2.2rem 0 .8rem;padding-bottom:.4rem;
                    border-bottom:1px solid rgba(128,128,128,.3)}
    .summary {font-size:1.05rem;line-height:1.6;padding:1rem 1.2rem;border-radius:10px;
              background:rgba(42,111,219,.08);border-left:4px solid #2a6fdb}
    .anomaly {padding:.8rem 1rem;margin-bottom:.6rem;border-radius:10px;font-weight:500;
              background:rgba(217,48,37,.10);border:1px solid rgba(217,48,37,.45);border-left:6px solid #d93025}
    .audit-id {margin:1rem 0 .2rem;padding:.7rem 1rem;border-radius:10px;border:1px dashed rgba(128,128,128,.45);
               font-size:.95rem;display:flex;flex-wrap:wrap;gap:.3rem 1rem;align-items:center}
    .audit-id b {font-family:monospace;font-size:1.05rem;letter-spacing:.03em}
    .audit-id span {opacity:.65;font-size:.85rem}
    .user-badge {text-align:right;font-weight:600;margin-bottom:.3rem}
    .role-pill {display:inline-block;margin-left:.4rem;padding:.05rem .5rem;border-radius:999px;font-size:.72rem;
                font-weight:700;background:rgba(42,111,219,.12);color:#2a6fdb;vertical-align:middle}
    .list-row {display:flex;justify-content:space-between;padding:.45rem 0;border-top:1px solid rgba(128,128,128,.18)}
    .list-row span:last-child {font-weight:700}
    .dot {display:inline-block;width:.65rem;height:.65rem;border-radius:50%;margin-right:.45rem;vertical-align:middle}
    /* Infobulle des graphiques : fond blanc, coins arrondis, ombre douce */
    #vg-tooltip-element, .vg-tooltip {background:#fff !important;color:#1f2937 !important;
        border:1px solid #e6e9ef !important;border-radius:10px !important;padding:.55rem .8rem !important;
        box-shadow:0 8px 24px rgba(15,23,42,.14) !important;font-family:inherit !important;font-size:13px !important}
    #vg-tooltip-element h2, .vg-tooltip h2 {font-size:12px !important;font-weight:600 !important;color:#6b7280 !important;
        margin:0 0 .25rem !important}
    #vg-tooltip-element td.key, .vg-tooltip td.key {color:#6b7280 !important;padding-right:.6rem !important}
    #vg-tooltip-element td.value, .vg-tooltip td.value {font-weight:700 !important;max-width:260px;white-space:normal}
    </style>"""


def apply_style():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def fmt_fcfa(value):
    if isinstance(value, (int, float)):
        return f"{value:,.0f} FCFA".replace(",", " ")
    return str(value)


def section(title):
    st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)


def login_card(key, icon, title, subtitle, role_required=None):
    """Formulaire de connexion centré. Renvoie l'utilisateur connecté (dict) ou None.

    role_required : si précisé, un compte valide mais d'un autre rôle est refusé."""
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown(
            f"<div style='text-align:center;margin-top:3rem;font-size:3rem'>{icon}</div>"
            f"<h2 style='text-align:center;margin:.2rem 0 0'>{esc(title)}</h2>"
            f"<p style='text-align:center;opacity:.65;margin-bottom:1.5rem'>{esc(subtitle)}</p>",
            unsafe_allow_html=True,
        )
        with st.form(key):
            login = st.text_input("Identifiant", placeholder="ex. prenom.nom")
            password = st.text_input("Mot de passe", type="password")
            submitted = st.form_submit_button("Se connecter", type="primary", width="stretch")
        if not submitted:
            return None
        agent = authenticate(login, password)
        if not agent:
            st.error("❌ Identifiant ou mot de passe incorrect. Vérifiez vos informations et réessayez.")
            return None
        if role_required and agent["role"] != role_required:
            st.error("⛔ Accès refusé : cet espace est réservé aux administrateurs.")
            return None
        return agent


def user_header(title, user, on_logout):
    """Titre de page + nom de l'utilisateur connecté et bouton de déconnexion à droite."""
    left, right = st.columns([5, 1.2], vertical_alignment="center")
    left.title(title)
    with right:
        role = f"<span class='role-pill'>{esc(user['role'])}</span>" if user.get("role") == ROLE_ADMIN else ""
        st.markdown(f"<div class='user-badge'>👤 {esc(user['nom'])}{role}</div>", unsafe_allow_html=True)
        if st.button("Se déconnecter", width="stretch", key=f"logout_{title}"):
            on_logout()
            st.rerun()
