"""
community.py — Personne 1
Stockage SQLite des avis, profils et demandes de contact.
Aucun document justificatif n'est jamais stocké : seulement verified=0/1.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("COMMUNITY_DB", os.path.join(ROOT, "community.db"))
SEED_PATH = os.path.join(ROOT, "seed", "seed_community.json")

RATING_KEYS = ["overall", "landlord_responsiveness", "maintenance", "noise", "safety"]
ROLES = {"current_tenant", "former_tenant", "seeking_roommate", "has_room", "seeking_housing"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  blklot TEXT NOT NULL,
  pseudo TEXT NOT NULL,
  period_start TEXT,
  period_end TEXT,
  ratings TEXT NOT NULL,          -- JSON
  text TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'published',
  demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_blklot ON reviews(blklot);

CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pseudo TEXT NOT NULL,
  role TEXT NOT NULL,
  blklot TEXT,
  neighborhood TEXT,
  budget_min INTEGER,
  budget_max INTEGER,
  move_in TEXT,
  bio TEXT,
  lifestyle TEXT,                 -- JSON
  open_to_contact INTEGER NOT NULL DEFAULT 0,
  verified INTEGER NOT NULL DEFAULT 0,
  demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profiles_blklot ON profiles(blklot);

CREATE TABLE IF NOT EXISTS contact_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_profile_id INTEGER NOT NULL,
  to_profile_id INTEGER NOT NULL,
  message TEXT,
  created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- conversions
def _review(row) -> dict:
    d = dict(row)
    d["ratings"] = json.loads(d["ratings"])
    d["verified"] = bool(d["verified"])
    d["demo"] = bool(d["demo"])
    return d


def _profile(row) -> dict:
    d = dict(row)
    d["lifestyle"] = json.loads(d["lifestyle"] or "{}")
    for k in ("open_to_contact", "verified", "demo"):
        d[k] = bool(d[k])
    d.pop("created_at", None)
    return d


# ---------------------------------------------------------------- validation
class ValidationError(ValueError):
    pass


def validate_review(r: dict) -> dict:
    for k in ("blklot", "pseudo", "text", "ratings"):
        if not r.get(k):
            raise ValidationError(f"champ manquant : {k}")
    ratings = {}
    for k in RATING_KEYS:
        v = r["ratings"].get(k)
        if v is None:
            continue
        v = int(v)
        if not 1 <= v <= 5:
            raise ValidationError(f"note {k} hors de 1-5")
        ratings[k] = v
    if "overall" not in ratings:
        raise ValidationError("note overall obligatoire")
    if len(r["text"]) > 3000:
        raise ValidationError("texte trop long (3000 caractères max)")
    return {**r, "ratings": ratings, "pseudo": str(r["pseudo"])[:40]}


def validate_profile(p: dict) -> dict:
    for k in ("pseudo", "role"):
        if not p.get(k):
            raise ValidationError(f"champ manquant : {k}")
    if p["role"] not in ROLES:
        raise ValidationError(f"role invalide, attendu : {sorted(ROLES)}")
    return {**p, "pseudo": str(p["pseudo"])[:40]}


# ---------------------------------------------------------------- avis
def add_review(review: dict, verified: bool = False, status: str = "published",
               demo: bool = False) -> dict:
    r = validate_review(review)
    with db() as c:
        cur = c.execute(
            "INSERT INTO reviews (blklot,pseudo,period_start,period_end,ratings,text,"
            "verified,status,demo,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["blklot"], r["pseudo"], r.get("period_start"), r.get("period_end"),
             json.dumps(r["ratings"]), r["text"], int(verified), status, int(demo), now()))
        row = c.execute("SELECT * FROM reviews WHERE id=?", (cur.lastrowid,)).fetchone()
    return _review(row)


def get_reviews(blklot: str, limit: int = 20) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT * FROM reviews WHERE blklot=? AND status='published' "
            "ORDER BY verified DESC, created_at DESC LIMIT ?", (blklot, limit)).fetchall()
    return [_review(r) for r in rows]


def get_community(blklot: str) -> dict:
    reviews = get_reviews(blklot)
    with db() as c:
        all_rows = c.execute(
            "SELECT ratings, verified FROM reviews WHERE blklot=? AND status='published'",
            (blklot,)).fetchall()
        tenants = c.execute(
            "SELECT * FROM profiles WHERE blklot=? AND open_to_contact=1 "
            "AND role IN ('current_tenant','former_tenant') ORDER BY verified DESC, id DESC",
            (blklot,)).fetchall()

    avg = None
    if all_rows:
        avg = {}
        parsed = [json.loads(r["ratings"]) for r in all_rows]
        for k in RATING_KEYS:
            vals = [p[k] for p in parsed if k in p]
            avg[k] = round(sum(vals) / len(vals), 1) if vals else None

    return {
        "reviews": reviews,
        "avg_ratings": avg,
        "n_reviews": len(all_rows),
        "n_verified": sum(r["verified"] for r in all_rows),
        "contactable_tenants": [_profile(t) for t in tenants],
    }


# ---------------------------------------------------------------- profils
def add_profile(profile: dict, verified: bool = False, demo: bool = False) -> dict:
    p = validate_profile(profile)
    with db() as c:
        cur = c.execute(
            "INSERT INTO profiles (pseudo,role,blklot,neighborhood,budget_min,budget_max,"
            "move_in,bio,lifestyle,open_to_contact,verified,demo,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["pseudo"], p["role"], p.get("blklot"), p.get("neighborhood"),
             p.get("budget_min"), p.get("budget_max"), p.get("move_in"),
             (p.get("bio") or "")[:1000], json.dumps(p.get("lifestyle") or {}),
             int(bool(p.get("open_to_contact"))), int(verified), int(demo), now()))
        row = c.execute("SELECT * FROM profiles WHERE id=?", (cur.lastrowid,)).fetchone()
    return _profile(row)


def get_profile(profile_id: int) -> dict | None:
    with db() as c:
        row = c.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    return _profile(row) if row else None


def list_candidates(profile_id: int) -> list[dict]:
    """Tous les profils colocation sauf soi-même. Le filtrage fin est fait par llm.match_roommates."""
    with db() as c:
        rows = c.execute(
            "SELECT * FROM profiles WHERE id != ? AND role IN ('seeking_roommate','has_room')",
            (profile_id,)).fetchall()
    return [_profile(r) for r in rows]


# ---------------------------------------------------------------- contact
def add_contact_request(from_id: int, to_id: int, message: str) -> bool:
    if not get_profile(from_id) or not get_profile(to_id):
        raise ValidationError("profil inconnu")
    target = get_profile(to_id)
    if target["role"] in ("current_tenant", "former_tenant") and not target["open_to_contact"]:
        raise ValidationError("ce locataire n'accepte pas les demandes de contact")
    with db() as c:
        c.execute("INSERT INTO contact_requests (from_profile_id,to_profile_id,message,created_at) "
                  "VALUES (?,?,?,?)", (from_id, to_id, (message or "")[:1000], now()))
    return True


# ---------------------------------------------------------------- seed
def _split_period(period):
    """'2022-2024' -> ('2022', '2024') ; '2022-' -> ('2022', None)"""
    if not period:
        return None, None
    a, _, b = str(period).partition("-")
    return (a or None), (b or None)


def _adapt_review(r: dict, i: int) -> dict:
    """Accepte le format du seed de la Personne 3 (ratings repairs/landlord/value, period)."""
    if "pseudo" in r and "overall" in r.get("ratings", {}):
        return r
    rt = r.get("ratings", {})
    ratings = {
        "maintenance": rt.get("maintenance", rt.get("repairs")),
        "landlord_responsiveness": rt.get("landlord_responsiveness", rt.get("landlord")),
        "noise": rt.get("noise"),
        "safety": rt.get("safety"),
    }
    vals = [v for v in list(ratings.values()) + [rt.get("value")] if v]
    ratings["overall"] = rt.get("overall") or (round(sum(vals) / len(vals)) if vals else 3)
    start, end = _split_period(r.get("period"))
    return {**r, "pseudo": r.get("pseudo") or f"locataire-{r.get('id', i)}",
            "period_start": r.get("period_start", start), "period_end": r.get("period_end", end),
            "ratings": {k: v for k, v in ratings.items() if v}}


def _adapt_profile(p: dict) -> dict:
    rm = p.get("roommate") or {}
    if not rm:
        return p
    areas = rm.get("areas") or []
    return {**p,
            "neighborhood": p.get("neighborhood") or (areas[0] if areas else None),
            "budget_min": p.get("budget_min", rm.get("budget_min")),
            "budget_max": p.get("budget_max", rm.get("budget_max")),
            "move_in": p.get("move_in") or (rm.get("move_in") or "")[:7] or None,
            "lifestyle": p.get("lifestyle") or rm,
            "open_to_contact": p.get("open_to_contact", True)}


def load_seed(path: str = SEED_PATH) -> int:
    """Charge les données de démo si la base est vide. Renvoie le nb d'éléments chargés."""
    init_db()
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] > 0:
            return 0
    if not os.path.exists(path):
        print(f"[community] pas de seed trouvé à {path}")
        return 0
    with open(path, encoding="utf-8") as f:
        seed = json.load(f)
    n = 0
    for i, r in enumerate(seed.get("reviews", [])):
        add_review(_adapt_review(r, i), verified=bool(r.get("verified")), demo=True)
        n += 1
    for p in seed.get("profiles", []):
        p = _adapt_profile(p)
        p.setdefault("open_to_contact", p.get("role") in ("current_tenant", "former_tenant"))
        add_profile(p, verified=bool(p.get("verified")), demo=True)
        n += 1
    print(f"[community] {n} éléments de démo chargés")
    return n
