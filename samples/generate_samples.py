"""Génère 3 profils de test fictifs (TXT + PDF) : régulier, irrégulier, suspect."""
import random
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent
START = datetime(2026, 6, 1)


def fmt(amount):
    return f"{amount:,}".replace(",", " ") + " FCFA"


def at(day, hour, minute):
    return START + timedelta(days=day, hours=hour, minutes=minute)


def regulier(rng):
    tx = []
    for d in range(92):
        growth = 1 + d / 300
        for _ in range(rng.randint(2, 4)):
            tx.append((at(d, rng.randint(8, 19), rng.randint(0, 59)), int(rng.randint(8, 35) * 1000 * growth), "Encaissement"))
        if at(d, 0, 0).weekday() == 0:
            tx.append((at(d, 10, rng.randint(0, 59)), rng.randint(150, 200) * 1000, "Paiement fournisseur"))
        if d in (29, 60, 90):
            tx.append((at(d, 17, 30), 250_000, "Retrait"))
    return tx


def irregulier(rng):
    tx = []
    for d in range(92):
        if 35 <= d < 52 or rng.random() > 0.35:
            continue
        decline = max(0.3, 1 - d / 120)
        for _ in range(rng.randint(1, 5)):
            tx.append((at(d, rng.randint(7, 21), rng.randint(0, 59)), int(rng.randint(2, 150) * 1000 * decline), "Encaissement"))
        if rng.random() < 0.3:
            tx.append((at(d, 12, rng.randint(0, 59)), rng.randint(50, 300) * 1000, "Retrait"))
    return tx


def suspect(rng):
    tx = [t for t in regulier(rng) if t[0] < at(60, 0, 0)]
    tx += [
        (at(70, 2, 14), 4_500_000, "Encaissement"),
        (at(70, 2, 41), 4_450_000, "Transfert sortant"),
        (at(75, 23, 5), 490_000, "Encaissement"),
        (at(75, 23, 9), 490_000, "Encaissement"),
        (at(75, 23, 12), 495_000, "Encaissement"),
        (at(75, 23, 20), 490_000, "Encaissement"),
        (at(76, 0, 3), 1_900_000, "Retrait"),
        (at(82, 3, 30), 2_000_000, "Encaissement"),
        (at(82, 3, 30), 2_000_000, "Encaissement"),
        (at(83, 9, 0), 3_950_000, "Transfert sortant"),
    ]
    return tx


def write(name, title, tx):
    tx.sort()
    lines = ["Date | Montant | Type"] + [f"{d:%d/%m/%Y %H:%M} | {fmt(m)} | {t}" for d, m, t in tx]
    (OUT / f"{name}.txt").write_text(f"{title}\n\n" + "\n".join(lines), encoding="utf-8")

    c = canvas.Canvas(str(OUT / f"{name}.pdf"), pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, title)
    y -= 30
    c.setFont("Helvetica", 9)
    for line in lines:
        if y < 40:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = 800
        c.drawString(40, y, line)
        y -= 13
    c.save()


if __name__ == "__main__":
    write("profil_regulier", "Relevé compte marchand Wave Business - Boutique Awa (FICTIF)", regulier(random.Random(1)))
    write("profil_irregulier", "Relevé compte marchand Orange Money Business - Kiosque Yao (FICTIF)", irregulier(random.Random(2)))
    write("profil_suspect", "Relevé compte marchand MTN MoMo Business - Ets Koné (FICTIF)", suspect(random.Random(3)))
    print("Profils générés dans", OUT)
