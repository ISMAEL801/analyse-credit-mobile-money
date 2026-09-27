"""Extraction du texte, parsing des transactions et analyse via l'API Groq (compatible OpenAI)."""
import io
import json
import os
import re

import numpy as np
import pandas as pd
import pdfplumber
from openai import OpenAI

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_CHARS = 12_000
MAX_DETAILED_ROWS = 120
MAX_IDLE_DAYS = 10  # au-delà, une interruption d'activité est toujours signalée comme anomalie
NON_TROUVE = "non trouvé"

FIELDS = [
    "nombre_transactions",
    "periode_couverte",
    "montant_moyen",
    "regularite",
    "tendance",
    "anomalies_detectees",
    "transactions_suspectes",
    "signal_global",
    "resume_texte",
]

SYSTEM_PROMPT = """Tu es un analyste financier qui examine l'historique de transactions d'un compte marchand Mobile Money
(Wave Business, Orange Money Business, MTN MoMo Business) en Côte d'Ivoire. Les montants sont en FCFA.

Ton rôle est UNIQUEMENT de présenter une analyse objective des données. Tu ne décides JAMAIS si le client
est éligible à un crédit : cette décision revient à un analyste crédit humain.

Règles strictes :
- Base-toi exclusivement sur les données fournies. N'invente aucune transaction, date ou montant.
- Si une information ne peut pas être déterminée à partir des données, mets exactement "non trouvé".
- Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après, sans bloc markdown.

Format attendu :
{
  "nombre_transactions": <entier ou "non trouvé">,
  "periode_couverte": "<JJ/MM/AAAA au JJ/MM/AAAA ou \\"non trouvé\\">",
  "montant_moyen": <nombre en FCFA ou "non trouvé">,
  "regularite": "régulier" | "irrégulier" | "très instable" | "non trouvé",
  "tendance": "stable" | "croissante" | "décroissante" | "non trouvé",
  "anomalies_detectees": ["<description courte et factuelle de chaque anomalie>"],
  "transactions_suspectes": [{"date": "<date telle qu'écrite dans les données>", "montant": <nombre>, "raison": "<courte>"}],
  "signal_global": "favorable" | "à vérifier" | "suspect",
  "resume_texte": "<2 à 3 phrases factuelles résumant l'analyse>"
}

Tu reçois des "indicateurs calculés" exacts. Utilise-les comme source unique pour tous les chiffres
(nombre, période, moyenne, interruptions, doublons, montants atypiques, horaires) : ne recalcule rien toi-même,
et ne cite dans les anomalies que des faits présents dans ces indicateurs ou dans le texte.

Définitions :
- regularite : "régulier" = activité presque quotidienne ; "irrégulier" = nombreux jours sans activité ou
  interruptions de plus d'une semaine ; "très instable" = activité sporadique et montants très dispersés.
- tendance : reprends exactement la tendance des entrées fournie dans les indicateurs calculés.
- anomalies : entrées importantes ressorties rapidement (allers-retours), fractionnement (plusieurs montants
  proches juste sous un seuil rond, ex. 490 000), doublons exacts, montants très atypiques, opérations de nuit
  d'un montant important, interruption d'activité de plus de 10 jours. Liste vide [] si aucune.
- Ne sont PAS des anomalies : les dépenses courantes de gestion (paiements fournisseurs réguliers, retraits
  périodiques) cohérentes avec le niveau d'activité, ni des montants identiques fortuits.
- signal_global : "favorable" = activité régulière sans anomalie ; "à vérifier" = activité irrégulière,
  interruption longue ou données insuffisantes, sans signe de manipulation ; "suspect" = au moins un
  aller-retour, fractionnement, doublon ou gros mouvement nocturne pouvant indiquer une manipulation.
  Ce signal est une indication d'analyse, pas une décision de crédit."""


def extract_pdf_text(file) -> str:
    with pdfplumber.open(file) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def extract_document_text(filename: str, data: bytes) -> str:
    """Texte d'un document importé (PDF, ou texte brut pour .txt/.csv)."""
    if filename.lower().endswith(".pdf"):
        return extract_pdf_text(io.BytesIO(data))
    return data.decode("utf-8", "ignore")


DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")

# --- Détection du client dans l'en-tête du document ---
MAX_HEADER_LINES = 15
CLIENT_LABEL_RE = re.compile(
    r"^(?:nom(?:\s+du\s+(?:client|commer[çc]ant|marchand|titulaire))?|client|titulaire(?:\s+du\s+compte)?|"
    r"raison\s+sociale|commer[çc]ante?|marchand|enseigne|b[ée]n[ée]ficiaire)\s*[:–-]\s*(.+)$", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"compte\s+marchand\b\s*[:–-]?\s*(.*)$", re.IGNORECASE)
BUSINESS_RE = re.compile(
    r"\b(commer[çc]ante?|boutique|kiosque|ets|[ée]tablissements?|soci[ée]t[ée]|sarl|magasin|restaurant|maquis|"
    r"pharmacie|quincaillerie|sup[ée]rette|alimentation|atelier|salon|cabinet|entreprise)\b", re.IGNORECASE)
PROVIDER_RE = re.compile(r"\b(wave|orange|mtn|moov)(\s+(money|momo))?(\s+business)?\b", re.IGNORECASE)


def _is_transaction_line(line: str) -> bool:
    parts = [p.strip() for p in re.split(r"\s*[|;\t]\s*", line) if p.strip()]
    return len(parts) >= 3 and bool(DATE_RE.search(parts[0]))


def _header_lines(text: str) -> list:
    """Lignes non vides situées avant le tableau de transactions (en-tête du document)."""
    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if _is_transaction_line(line) or re.match(r"date\s*[|;\t]", line, re.IGNORECASE):
            break
        lines.append(line)
        if len(lines) >= MAX_HEADER_LINES:
            break
    return lines


def _clean_client(value: str):
    value = " ".join(value.split()).strip(" -–:,.;|")
    if len(value) < 3 or not re.search(r"[A-Za-zÀ-ÿ]", value):
        return None
    return value[:100]


def detect_client(text: str):
    """Cherche un nom ou une référence de client/commerçant dans l'en-tête du document. Renvoie None si rien de clair."""
    lines = _header_lines(text)
    for line in lines:  # 1. libellé explicite : "Client : ...", "Titulaire : ...", "Raison sociale : ..."
        m = CLIENT_LABEL_RE.match(line)
        if m and _clean_client(m.group(1)):
            return _clean_client(m.group(1))
    for line in lines:  # 2. "Compte marchand X" / "Relevé compte marchand Wave Business - X"
        m = ACCOUNT_RE.search(line)
        if m:
            rest = line.rsplit(" - ", 1)[1] if " - " in line else m.group(1)
            rest = _clean_client(PROVIDER_RE.sub("", rest))
            if rest:
                return rest
    for line in lines:  # 3. description d'activité : "Commerçante de produits alimentaires, Abidjan", "Boutique ..."
        if BUSINESS_RE.search(line):
            cleaned = _clean_client(line.rsplit(" - ", 1)[-1])
            if cleaned:
                return cleaned
    return None


def parse_transactions(text: str) -> pd.DataFrame:
    """Parse les lignes 'Date | Montant | Type' (séparateurs | ; ou tabulation)."""
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in re.split(r"\s*[|;\t]\s*", line) if p.strip()]
        if len(parts) < 3 or not DATE_RE.search(parts[0]):
            continue
        digits = re.sub(r"[^\d-]", "", parts[1])
        if not digits.lstrip("-"):
            continue
        iso = bool(re.match(r"\d{4}-", parts[0]))
        date = pd.to_datetime(parts[0], dayfirst=not iso, errors="coerce")
        if pd.isna(date):
            continue
        rows.append({"Date": date, "Date (brute)": parts[0], "Montant": int(digits), "Type": parts[2]})
    df = pd.DataFrame(rows, columns=["Date", "Date (brute)", "Montant", "Type"])
    return df.sort_values("Date").reset_index(drop=True)


OUTFLOW_RE = re.compile(r"retrait|sortant|envoi|paiement|débit|debit|achat", re.IGNORECASE)


TREND_THRESHOLD = 0.10  # variation relative au-delà de laquelle la tendance est croissante / décroissante
MIN_MONTH_DAYS = 7  # un mois couvert moins de 7 jours est trop court pour peser dans la tendance


def monthly_entries(df: pd.DataFrame) -> pd.DataFrame:
    """Total des entrées (argent reçu) par mois, avec le nombre de jours du mois couverts par le relevé."""
    cols = ["mois", "total", "jours_couverts", "jours_mois"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    entries = df[~df["Type"].str.contains(OUTFLOW_RE)]
    start, end = df["Date"].min().normalize(), df["Date"].max().normalize()
    rows = []
    for month in pd.period_range(start, end, freq="M"):
        first, last = max(month.start_time, start), min(month.end_time.normalize(), end)
        total = entries.loc[entries["Date"].dt.to_period("M") == month, "Montant"].sum()
        rows.append({"mois": month, "total": int(total), "jours_couverts": (last - first).days + 1,
                     "jours_mois": month.days_in_month})
    return pd.DataFrame(rows, columns=cols)


def compute_trend(df: pd.DataFrame):
    """Tendance des entrées ('croissante' / 'décroissante' / 'stable' / 'non trouvé') et variation relative.

    On compare la moyenne journalière des entrées de chaque mois (un mois partiel est ramené à ses jours couverts),
    via la pente d'une régression linéaire rapportée à la moyenne, sur toute la période."""
    months = monthly_entries(df)
    months = months[months["jours_couverts"] >= MIN_MONTH_DAYS]
    if len(months) < 2:
        return NON_TROUVE, None
    daily = (months["total"] / months["jours_couverts"]).to_numpy(dtype=float)
    if daily.mean() == 0:
        return NON_TROUVE, None
    slope = np.polyfit(np.arange(len(daily)), daily, 1)[0]
    change = slope * (len(daily) - 1) / daily.mean()
    if change > TREND_THRESHOLD:
        return "croissante", change
    if change < -TREND_THRESHOLD:
        return "décroissante", change
    return "stable", change


ATYPICAL_FACTOR = 10  # une entrée est atypique si elle dépasse 10 fois la médiane des entrées


def atypical_entries(df: pd.DataFrame) -> pd.DataFrame:
    """Entrées d'un montant supérieur à ATYPICAL_FACTOR fois la médiane des entrées."""
    entries = df[~df["Type"].str.contains(OUTFLOW_RE)]
    if entries.empty:
        return entries
    return entries[entries["Montant"] > ATYPICAL_FACTOR * entries["Montant"].median()]


def rise_driven_by_atypical(df: pd.DataFrame) -> bool:
    """Vrai si la tendance est croissante mais ne l'est plus une fois les entrées atypiques retirées."""
    if df.empty or compute_trend(df)[0] != "croissante":
        return False
    atypical = atypical_entries(df)
    return not atypical.empty and compute_trend(df.drop(atypical.index))[0] != "croissante"


def _tx(row) -> str:
    return f"{row['Date (brute)']} {row['Montant']} FCFA ({row['Type']})"


def longest_gap(df: pd.DataFrame):
    """Plus longue période sans transaction : (jours d'inactivité, dernier jour actif, jour de reprise) ou None."""
    days = df["Date"].dt.normalize().drop_duplicates().reset_index(drop=True)
    gaps = days.diff().dt.days
    if df.empty or not gaps.max() > 1:
        return None
    i = gaps.idxmax()
    return int(gaps[i]) - 1, days[i - 1], days[i]


def local_facts(df: pd.DataFrame) -> str:
    """Indicateurs calculés en Python, fournis au modèle pour qu'il interprète sans calculer."""
    if df.empty:
        return "Aucune transaction n'a pu être lue automatiquement dans le document."
    df = df.assign(Sortie=df["Type"].str.contains(OUTFLOW_RE))
    entries = df[~df["Sortie"]]
    median = entries["Montant"].median() if not entries.empty else df["Montant"].median()
    facts = [
        f"Nombre de transactions : {len(df)} ({len(entries)} entrées, {int(df['Sortie'].sum())} sorties)",
        f"Période : {df['Date'].min():%d/%m/%Y} au {df['Date'].max():%d/%m/%Y}",
        f"Montant moyen : {df['Montant'].mean():.0f} FCFA ; montant médian des entrées : {median:.0f} FCFA",
    ]

    days = df["Date"].dt.normalize().drop_duplicates()
    span = (days.max() - days.min()).days + 1
    facts.append(f"Jours avec activité : {len(days)} sur {span} jours")
    gap = longest_gap(df)
    if gap:
        facts.append(f"Plus longue interruption : {gap[0]} jours sans transaction (du {gap[1]:%d/%m/%Y} au {gap[2]:%d/%m/%Y})")

    monthly = entries.groupby(entries["Date"].dt.to_period("M"))["Montant"].agg(["sum", "count"])
    facts.append("Entrées par mois : " + " ; ".join(
        f"{p.strftime('%m/%Y')} = {int(r['sum'])} FCFA en {int(r['count'])} opérations" for p, r in monthly.iterrows()
    ))
    trend, change = compute_trend(df)
    facts.append(f"Tendance des entrées (moyenne journalière par mois, mois partiels corrigés) : {trend}"
                 + (f" ({change:+.0%} sur la période)" if change is not None else "")
                 + (" — hausse portée par des montants atypiques : sans eux, les entrées ne sont pas en hausse"
                    if rise_driven_by_atypical(df) else ""))

    atypical = atypical_entries(df)
    facts.append(f"Entrées d'un montant > {ATYPICAL_FACTOR}x la médiane des entrées : {len(atypical)}" + (
        " → " + " ; ".join(_tx(r) for _, r in atypical.head(10).iterrows()) if len(atypical) else ""))

    night = df[df["Date"].dt.hour < 5] if (df["Date"].dt.hour > 0).any() else df.iloc[0:0]
    facts.append(f"Transactions entre 00h et 05h : {len(night)}" + (
        " → " + " ; ".join(_tx(r) for _, r in night.head(10).iterrows()) if len(night) else ""))

    dups = df[df.duplicated(["Date", "Montant", "Type"], keep=False)]
    facts.append(f"Doublons exacts (même date/heure, montant, type) : {len(dups)}" + (
        " → " + " ; ".join(_tx(r) for _, r in dups.head(10).iterrows()) if len(dups) else ""))

    pairs = []
    for _, e in atypical.iterrows():
        out = df[df["Sortie"] & (df["Date"] >= e["Date"]) & (df["Date"] <= e["Date"] + pd.Timedelta(hours=48))
                 & (df["Montant"] >= 0.8 * e["Montant"])]
        if not out.empty:
            pairs.append(f"entrée {_tx(e)} suivie de sortie {_tx(out.iloc[0])}")
    facts.append(f"Entrées importantes ressorties en moins de 48h : {len(pairs)}" + (" → " + " ; ".join(pairs) if pairs else ""))

    return "\n".join(f"- {f}" for f in facts)


def compact_history(df: pd.DataFrame, text: str) -> str:
    """Historique réduit pour tenir dans la limite de tokens/minute de Groq (offre gratuite : 8000)."""
    if df.empty:
        return "Texte brut du document :\n" + text
    if len(df) <= MAX_DETAILED_ROWS:
        return "Historique (date;montant FCFA;type) :\n" + "\n".join(
            f"{d};{m};{t}" for d, m, t in df[["Date (brute)", "Montant", "Type"]].itertuples(index=False))
    lines = []
    for day, g in df.groupby(df["Date"].dt.date):
        out = g[g["Type"].str.contains(OUTFLOW_RE)]
        inc = g.drop(out.index)
        line = f"{day:%d/%m/%Y}: {len(inc)} entrées = {inc['Montant'].sum()}"
        if not out.empty:
            line += " | sorties: " + ", ".join(f"{m} ({t})" for m, t in out[["Montant", "Type"]].itertuples(index=False))
        lines.append(line)
    return "Historique résumé par jour (montants en FCFA) :\n" + "\n".join(lines)


def _extract_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Réponse du modèle sans JSON : {content[:300]}")
    return json.loads(content[start : end + 1])


def _normalize(data: dict) -> dict:
    result = {f: data.get(f, NON_TROUVE) for f in FIELDS}
    for f in ("anomalies_detectees", "transactions_suspectes"):
        if not isinstance(result[f], list):
            result[f] = []
    if result["signal_global"] not in ("favorable", "à vérifier", "suspect"):
        result["signal_global"] = "à vérifier"
    if result["regularite"] not in ("régulier", "irrégulier", "très instable"):  # valeur hors liste -> non trouvé
        result["regularite"] = NON_TROUVE
    return result


def analyze(text: str, df: pd.DataFrame) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY absente : ajoute-la dans le fichier .env")
    client = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key)
    user_msg = (
        "Indicateurs calculés automatiquement à partir du document (exacts) :\n"
        f"{local_facts(df)}\n\n"
        f"{compact_history(df, text)[:MAX_CHARS]}"
    )
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
        temperature=0,
        max_tokens=2500,  # compté dans la limite de tokens/minute de Groq
        response_format={"type": "json_object"},
        reasoning_effort="low",
    )
    result = _normalize(_extract_json(response.choices[0].message.content))
    # La tendance est calculée par Python (reproductible) : elle remplace celle du modèle
    result["tendance"] = compute_trend(df)[0] if not df.empty else NON_TROUVE
    return _add_computed_anomalies(result, df)


def _add_computed_anomalies(result: dict, df: pd.DataFrame) -> dict:
    """Garantit la présence des anomalies calculées par Python, même si l'IA les omet."""
    gap = longest_gap(df) if not df.empty else None
    if gap and gap[0] > MAX_IDLE_DAYS:
        if not any("interruption" in str(a).lower() for a in result["anomalies_detectees"]):
            result["anomalies_detectees"].append(
                f"Interruption d'activité de {gap[0]} jours sans transaction (du {gap[1]:%d/%m/%Y} au {gap[2]:%d/%m/%Y})"
            )
        if result["signal_global"] == "favorable":
            result["signal_global"] = "à vérifier"
    return result
