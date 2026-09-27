"""Authentification simple (prototype) : comptes dans agents.json, mots de passe hachés (PBKDF2).

Chaque compte a un rôle : "analyste crédit" (interface d'analyse) ou "admin" (accès en plus à l'espace /Admin).

Ajouter ou modifier un compte :
    python auth.py <identifiant> "<Nom complet>" <mot de passe> ["analyste crédit"|admin]
"""
import hashlib
import hmac
import json
import os
import secrets
import sys

AGENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")
ITERATIONS = 200_000


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def load_agents() -> dict:
    if not os.path.exists(AGENTS_FILE):
        return {}
    with open(AGENTS_FILE, encoding="utf-8") as f:
        return json.load(f)


ROLE_ANALYST, ROLE_ADMIN = "analyste crédit", "admin"
ROLES = (ROLE_ANALYST, ROLE_ADMIN)
LEGACY_ROLES = {"agent": ROLE_ANALYST, "développeur": ROLE_ADMIN}  # anciens noms de rôles


def authenticate(login: str, password: str):
    """Renvoie {'login', 'nom', 'role'} si les identifiants sont corrects, sinon None."""
    login = (login or "").strip().lower()
    agent = load_agents().get(login)
    # On calcule le hachage même si l'identifiant est inconnu, pour ne pas révéler son existence par le temps de réponse
    salt, expected = (agent["salt"], agent["hash"]) if agent else ("00" * 16, "")
    if hmac.compare_digest(_hash(password or "", salt), expected) and agent:
        role = agent.get("role", ROLE_ANALYST)
        return {"login": login, "nom": agent["nom"], "role": LEGACY_ROLES.get(role, role)}
    return None


def add_agent(login: str, nom: str, password: str, role: str = ROLE_ANALYST):
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role} (attendu : {', '.join(ROLES)})")
    agents = load_agents()
    salt = secrets.token_hex(16)
    agents[login.strip().lower()] = {"nom": nom, "role": role, "salt": salt, "hash": _hash(password, salt)}
    with open(AGENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(agents, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    add_agent(*sys.argv[1:5])
    print(f"Compte '{sys.argv[1]}' enregistré dans {AGENTS_FILE}")
