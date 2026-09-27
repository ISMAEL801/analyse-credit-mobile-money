"""Journal d'audit des analyses (SQLite, un seul fichier local)."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

import pandas as pd

from auth import load_agents

DB_PATH = os.getenv("HISTORIQUE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "historique.db"))

# colonne SQL -> intitulé affiché / exporté
COLUMNS = {
    "id": "ID",
    "date": "Date",
    "agent": "Analyste",
    "client": "Client",
    "signal": "Signal global",
    "regularite": "Régularité",
    "tendance": "Tendance",
    "nb_anomalies": "Nb anomalies",
    "anomalies": "Anomalies détectées",
    "nombre_transactions": "Transactions",
    "periode": "Période couverte",
    "montant_moyen": "Montant moyen (FCFA)",
    "resume": "Résumé de l'IA",
}
# colonnes ajoutées après la première version : ajoutées automatiquement aux bases existantes
MIGRATIONS = {"agent_login": "TEXT", "texte": "TEXT"}


def _connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            num INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            agent TEXT NOT NULL,
            client TEXT NOT NULL,
            signal TEXT, regularite TEXT, tendance TEXT,
            nb_anomalies INTEGER, anomalies TEXT,
            nombre_transactions TEXT, periode TEXT, montant_moyen TEXT,
            resume TEXT,
            resultat_json TEXT
        )""")
    existing = {row[1] for row in con.execute("PRAGMA table_info(analyses)")}
    for column, sql_type in MIGRATIONS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE analyses ADD COLUMN {column} {sql_type}")
    # Analyses enregistrées avant l'ajout de agent_login : on retrouve l'identifiant à partir du nom de l'agent
    if con.execute("SELECT 1 FROM analyses WHERE agent_login IS NULL LIMIT 1").fetchone():
        for login, info in load_agents().items():
            con.execute("UPDATE analyses SET agent_login = ? WHERE agent_login IS NULL AND agent = ?", (login, info["nom"]))
        con.commit()
    return con


def save_analysis(agent: dict, client: str, result: dict, text: str = "", when: datetime = None) -> str:
    """Enregistre une analyse et renvoie son identifiant unique (A-AAAA-NNNN, numérotation par année).
    when : date de l'analyse (par défaut maintenant ; précisée uniquement pour les données de démonstration)."""
    now = when or datetime.now()
    prefix = f"A-{now.year}-"
    anomalies = [str(a) for a in result.get("anomalies_detectees", [])]
    with closing(_connect()) as con:
        con.execute("BEGIN IMMEDIATE")  # verrou : deux analyses simultanées ne peuvent pas obtenir le même numéro
        last = con.execute("SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM analyses WHERE id LIKE ?",
                           (len(prefix) + 1, prefix + "%")).fetchone()[0]
        analysis_id = f"{prefix}{(last or 0) + 1:04d}"
        con.execute(
            "INSERT INTO analyses (id, date, agent, agent_login, client, signal, regularite, tendance, nb_anomalies,"
            " anomalies, nombre_transactions, periode, montant_moyen, resume, resultat_json, texte)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (analysis_id, now.strftime("%d/%m/%Y %H:%M:%S"), agent["nom"], agent["login"], client,
             result.get("signal_global"), result.get("regularite"), result.get("tendance"),
             len(anomalies), " | ".join(anomalies),
             str(result.get("nombre_transactions")), result.get("periode_couverte"), str(result.get("montant_moyen")),
             result.get("resume_texte"), json.dumps(result, ensure_ascii=False), text),
        )
        con.commit()
    return analysis_id


def load_history(agent_login: str = None) -> pd.DataFrame:
    """Analyses (de la plus récente à la plus ancienne), avec les intitulés lisibles.
    agent_login : si précisé, seulement les analyses de cet analyste ; sinon toutes (espace admin)."""
    query = f"SELECT {', '.join(COLUMNS)} FROM analyses"
    params = ()
    if agent_login:
        query += " WHERE agent_login = ?"
        params = (agent_login,)
    with closing(_connect()) as con:
        # tri par date réelle (la date est stockée au format JJ/MM/AAAA HH:MM:SS)
        order = " ORDER BY substr(date, 7, 4) || substr(date, 4, 2) || substr(date, 1, 2) || substr(date, 12) DESC, num DESC"
        df = pd.read_sql_query(query + order, con, params=params)
    return df.rename(columns=COLUMNS)


def history_csv(agent_login: str = None) -> bytes:
    """Export CSV pour Excel (séparateur ';' et encodage UTF-8 avec BOM, pour les accents et le format français)."""
    return load_history(agent_login).to_csv(sep=";", index=False).encode("utf-8-sig")


def load_analysis(analysis_id: str):
    """Détails techniques d'une analyse : résultat JSON complet de l'IA et texte extrait du document."""
    with closing(_connect()) as con:
        row = con.execute("SELECT resultat_json, texte, agent, client, date FROM analyses WHERE id = ?",
                          (analysis_id,)).fetchone()
    if not row:
        return None
    return {"resultat": json.loads(row[0] or "{}"), "texte": row[1] or "", "agent": row[2], "client": row[3],
            "date": row[4]}
