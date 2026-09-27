"""Données de démonstration : remplit l'historique avec des analyses fictives réparties entre les analystes crédit.

Les relevés sont générés aléatoirement (commerces fictifs). Les indicateurs (tendance, interruptions, anomalies…)
sont calculés par le vrai code Python de l'application ; le signal global et le résumé sont déduits de ces
indicateurs par des règles simples, SANS appel à l'IA. Chaque analyse est marquée « démo » (client suffixé
« (démo) » et "demo": true dans le résultat JSON).

    python seed_demo.py              ajoute les analyses de démonstration
    python seed_demo.py --supprimer  retire toutes les analyses de démonstration
"""
import random
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta

import pandas as pd

import db
from analyzer import (ATYPICAL_FACTOR, NON_TROUVE, OUTFLOW_RE, atypical_entries, compute_trend, longest_gap,
                      parse_transactions)
from auth import load_agents

START = datetime(2026, 6, 1)
OPERATORS = ["Wave Business", "Orange Money Business", "MTN MoMo Business", "Moov Money Business"]
# (analyste, client, type de profil)
DEMO = [
    ("awa.kone", "Boutique Chez Awa – Adjamé", "hausse"),
    ("awa.kone", "Maquis Chez Tantie – Yopougon", "stable"),
    ("awa.kone", "Quincaillerie Bamba – Abobo", "irregulier"),
    ("awa.kone", "Salon Belle Dame – Cocody", "hausse"),
    ("awa.kone", "Superette La Grâce – Koumassi", "suspect"),
    ("awa.kone", "Atelier Couture Fatou – Marcory", "baisse"),
    ("awa.kone", "Friperie Aya – Adjamé", "stable"),
    ("yao.kouassi", "Kiosque Yao – Port-Bouët", "irregulier"),
    ("yao.kouassi", "Restaurant Le Baobab – Plateau", "hausse"),
    ("yao.kouassi", "Ets Koné & Fils – Treichville", "suspect"),
    ("yao.kouassi", "Boulangerie Saint-Joseph – Bingerville", "stable"),
    ("yao.kouassi", "Garage Auto Soro – Yopougon", "instable"),
    ("yao.kouassi", "Alimentation Diallo – Adjamé", "hausse"),
    ("mariam.traore", "Poissonnerie Mama – Grand-Bassam", "stable"),
    ("mariam.traore", "Cabine Mobile Money Traoré – Abobo", "suspect"),
    ("mariam.traore", "Cyber Espace Kouadio – Cocody", "baisse"),
    ("mariam.traore", "Pagnes Adjoua – Treichville", "hausse"),
    ("mariam.traore", "Pharmacie du Carrefour – Marcory", "irregulier"),
]


def _at(day, rng, h0=8, h1=19):
    return START + timedelta(days=day, hours=rng.randint(h0, h1), minutes=rng.randint(0, 59))


def generate(kind, rng):
    """Liste de transactions (date, montant, type) sur 3 mois selon le type de profil."""
    growth = {"hausse": lambda d: 1 + d / rng.randint(220, 320), "stable": lambda d: 1,
              "baisse": lambda d: 1 - d / rng.randint(170, 230)}.get(kind, lambda d: 1)
    base = rng.randint(8, 20) * 1000
    tx = []
    for d in range(92):
        if kind == "irregulier" and (35 <= d < 35 + rng.randint(14, 20) or rng.random() > 0.4):
            continue
        if kind == "instable" and rng.random() > 0.2:
            continue
        spread = 6 if kind in ("instable", "irregulier") else 2.5
        for _ in range(rng.randint(1, 5) if kind in ("irregulier", "instable") else rng.randint(2, 4)):
            tx.append((_at(d, rng), int(base * rng.uniform(0.6, spread) * growth(d)), "Encaissement"))
        if (START + timedelta(days=d)).weekday() == 0 and kind not in ("instable",):
            tx.append((_at(d, rng, 9, 11), rng.randint(8, 14) * 10 * base // 100 * 10, "Paiement fournisseur"))
        if d in (29, 60, 90) and rng.random() < 0.8:
            tx.append((_at(d, rng, 16, 18), rng.randint(10, 25) * 10000, "Retrait"))
    if kind == "suspect":  # activité normale, puis opérations inhabituelles en août
        tx = [t for t in tx if t[0] < START + timedelta(days=60)]
        big = rng.randint(30, 50) * 100_000
        night = START + timedelta(days=rng.randint(66, 72), hours=2, minutes=rng.randint(0, 40))
        tx += [(night, big, "Encaissement"), (night + timedelta(minutes=27), big - 50_000, "Transfert sortant")]
        day = START + timedelta(days=rng.randint(74, 78), hours=23)
        tx += [(day + timedelta(minutes=m), a, "Encaissement") for m, a in [(5, 490_000), (9, 490_000), (12, 495_000)]]
        dup = START + timedelta(days=rng.randint(80, 84), hours=3, minutes=30)
        tx += [(dup, 2_000_000, "Encaissement"), (dup, 2_000_000, "Encaissement")]
    return sorted(tx)


def to_text(client, operator, tx):
    lines = [f"Relevé compte marchand {operator} - {client} (FICTIF)", "", "Date | Montant | Type"]
    lines += [f"{d:%d/%m/%Y %H:%M} | {f'{m:,}'.replace(',', ' ')} FCFA | {t}" for d, m, t in tx]
    return "\n".join(lines)


def fcfa(v):
    return f"{v:,.0f} FCFA".replace(",", " ")


def demo_result(df):
    """Résultat au même format que celui de l'IA, déduit des indicateurs calculés par Python."""
    days = df["Date"].dt.normalize().nunique()
    span = (df["Date"].max() - df["Date"].min()).days + 1
    ratio = days / span
    regularite = "régulier" if ratio >= 0.8 else "irrégulier" if ratio >= 0.35 else "très instable"
    trend, change = compute_trend(df)
    entries = df[~df["Type"].str.contains(OUTFLOW_RE)]
    anomalies, suspects, manipulation = [], [], False

    gap = longest_gap(df)
    if gap and gap[0] > 10:
        anomalies.append(f"Interruption d'activité de {gap[0]} jours sans transaction (du {gap[1]:%d/%m/%Y} au {gap[2]:%d/%m/%Y})")
    atypical = atypical_entries(df)
    if len(atypical):
        anomalies.append(f"{len(atypical)} entrée(s) supérieure(s) à {ATYPICAL_FACTOR} fois la médiane des entrées")
        suspects += [(r, "montant très supérieur à l'habitude") for _, r in atypical.iterrows()]
    night = df[df["Date"].dt.hour < 5]
    if len(night):
        anomalies.append(f"{len(night)} transaction(s) entre 00h et 05h")
        manipulation = True
        suspects += [(r, "opération nocturne") for _, r in night.iterrows()]
    dups = df[df.duplicated(["Date", "Montant", "Type"], keep=False)]
    if len(dups):
        r = dups.iloc[0]
        anomalies.append(f"Doublon exact le {r['Date (brute)']} de {fcfa(r['Montant'])}")
        manipulation = True
    for _, e in atypical.iterrows():
        out = df[df["Type"].str.contains(OUTFLOW_RE) & (df["Date"] >= e["Date"])
                 & (df["Date"] <= e["Date"] + pd.Timedelta(hours=48)) & (df["Montant"] >= 0.8 * e["Montant"])]
        if not out.empty:
            anomalies.append(f"Aller-retour : entrée de {fcfa(e['Montant'])} suivie d'une sortie de "
                             f"{fcfa(out.iloc[0]['Montant'])} en moins de 48 h")
            manipulation = True
            break
    same_day = atypical.groupby(atypical["Date"].dt.date)["Montant"].agg(["count", "min", "max"])
    if ((same_day["count"] >= 3) & (same_day["max"] <= same_day["min"] * 1.05)).any():
        anomalies.append("Plusieurs encaissements de montants proches le même jour (fractionnement possible)")
        manipulation = True

    if manipulation:
        signal = "suspect"
        resume = (f"Le compte présente des opérations inhabituelles ({anomalies[-1].lower()}, entre autres). "
                  "Ces éléments nécessitent une vérification approfondie avant toute décision.")
    elif anomalies or regularite != "régulier" or trend == "décroissante":
        signal = "à vérifier"
        resume = (f"Activité {regularite} : {days} jours actifs sur {span}, tendance des entrées {trend}"
                  + (f" ({change:+.0%})" if change is not None else "") + ". "
                  "Ces irrégularités méritent un examen attentif par l'analyste crédit.")
    else:
        signal = "favorable"
        resume = (f"Activité quasi quotidienne ({days} jours actifs sur {span}) avec un montant moyen de "
                  f"{fcfa(df['Montant'].mean())}. Tendance des entrées {trend}"
                  + (f" ({change:+.0%})" if change is not None else "") + ", sans anomalie notable.")
    return {
        "nombre_transactions": len(df),
        "periode_couverte": f"{df['Date'].min():%d/%m/%Y} au {df['Date'].max():%d/%m/%Y}",
        "montant_moyen": int(df["Montant"].mean()),
        "regularite": regularite,
        "tendance": trend if trend else NON_TROUVE,
        "anomalies_detectees": anomalies,
        "transactions_suspectes": [{"date": r["Date (brute)"], "montant": int(r["Montant"]), "raison": why}
                                   for r, why in suspects[:10]],
        "signal_global": signal,
        "resume_texte": resume,
        "demo": True,
        "source": "Donnée de démonstration générée sans appel à l'IA",
    }


def remove_demo():
    with closing(db._connect()) as con:
        n = con.execute("DELETE FROM analyses WHERE resultat_json LIKE '%\"demo\": true%'").rowcount
        con.commit()
    print(f"{n} analyse(s) de démonstration supprimée(s) de {db.DB_PATH}")


def seed():
    agents = load_agents()
    rng = random.Random(2026)
    first = (datetime.now() - timedelta(days=21)).replace(hour=0, minute=0, second=0, microsecond=0)
    # analyses étalées sur les 3 dernières semaines, en heures de bureau
    dates = sorted(first + timedelta(days=rng.randint(0, 20), hours=rng.randint(8, 17), minutes=rng.randint(0, 59))
                   for _ in DEMO)
    order = list(range(len(DEMO)))
    rng.shuffle(order)
    for when, i in zip(dates, order):
        login, client, kind = DEMO[i]
        tx = generate(kind, rng)
        text = to_text(client, rng.choice(OPERATORS), tx)
        df = parse_transactions(text)
        result = demo_result(df)
        agent = {"login": login, "nom": agents[login]["nom"]}
        aid = db.save_analysis(agent, f"{client} (démo)", result, text, when=when)
        print(f"{aid}  {when:%d/%m %H:%M}  {agent['nom']:<14} {kind:<10} -> {result['signal_global']:<10} "
              f"tendance {result['tendance']:<12} {len(result['anomalies_detectees'])} anomalie(s)  {client}")


if __name__ == "__main__":
    if "--supprimer" in sys.argv:
        remove_demo()
    else:
        seed()
