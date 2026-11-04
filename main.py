"""
main.py — Personne 1
Lancer :  uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import community  # noqa: E402
import data  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PROOF_BYTES = 10 * 1024 * 1024
LANGS = {"en", "es", "zh", "fr"}

# ---------------------------------------------------------------- llm.py (Personne 2) avec replis
try:
    import llm  # type: ignore
except ImportError:
    llm = None
    print("[main] llm.py absent : replis activés")


def _llm(name):
    return getattr(llm, name, None) if llm else None


def fallback_report(building: dict, reviews: list, lang: str) -> dict:
    """Rapport par règles simples, utilisé tant que llm.generate_report n'est pas prêt
    ou si l'appel Mistral échoue. Conforme au schéma Report."""
    s, p = building["stats"], building["profile"]
    unsafe = any(v["unsafe_building"] and v["status"] == "open" for v in building["violations"])
    signals = []
    if unsafe:
        signals.append({"level": "red", "title": "Open unsafe-building violation",
                        "detail": "DBI has an open violation flagged as unsafe.", "source": "violations"})
    if s["open_complaints"]:
        lvl = "red" if s["open_complaints"] >= 5 else "orange"
        signals.append({"level": lvl, "title": f"{s['open_complaints']} open DBI complaints",
                        "detail": f"{s['complaints_10y']} complaints in 10 years.", "source": "dbi_complaints"})
    elif s["complaints_10y"] == 0:
        signals.append({"level": "green", "title": "No DBI complaints in 10 years",
                        "detail": "No building complaint on file.", "source": "dbi_complaints"})
    if s["evictions_block_10y"]:
        signals.append({"level": "orange" if s["evictions_block_10y"] >= 5 else "green",
                        "title": f"{s['evictions_block_10y']} eviction notices on this block",
                        "detail": "Counted at the street-block level, not this building.",
                        "source": "evictions"})
    if p.get("likely_rent_controlled"):
        signals.append({"level": "green", "title": "Likely rent-controlled",
                        "detail": f"Built in {p['year_built']} with {p['units']} units (indicative only).",
                        "source": "tax_rolls"})
    overall = "red" if any(x["level"] == "red" for x in signals) else \
        "orange" if any(x["level"] == "orange" for x in signals) else "green"
    return {
        "overall": overall,
        "headline": f"{building['matched_address']}: {overall.upper()} — automatic summary",
        "summary": "Rule-based summary (AI report unavailable). See the signals and raw data below.",
        "signals": signals,
        "tenant_voice": None if len(reviews) < 2 else f"{len(reviews)} tenant reviews available below.",
        "questions_to_ask_landlord": [
            "Are there any open complaints or violations, and when will they be fixed?",
            "Is this unit covered by rent control?",
            "What is included in the rent (water, garbage, heat)?",
        ],
        "disclaimer": "This is not legal advice. Data from DataSF and tenant reviews.",
    }


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def fallback_moderation(text: str) -> dict:
    cleaned = PHONE_RE.sub("[removed]", EMAIL_RE.sub("[removed]", text))
    return {"ok": True, "cleaned_text": cleaned, "reason": "basic filter (AI moderation unavailable)"}


def fallback_match(seeker: dict, candidates: list) -> list:
    out = []
    for c in candidates:
        if seeker.get("budget_max") and c.get("budget_min") and c["budget_min"] > seeker["budget_max"]:
            continue
        if seeker.get("budget_min") and c.get("budget_max") and c["budget_max"] < seeker["budget_min"]:
            continue
        score = 0.5 + (0.25 if c.get("neighborhood") == seeker.get("neighborhood") else 0)
        out.append({"profile": c, "score": round(score, 2), "why": "Compatible budget (basic matching)."})
    return sorted(out, key=lambda x: -x["score"])[:10]


# ---------------------------------------------------------------- app
app = FastAPI(title="Know Your Building")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    community.load_seed()


class ContactIn(BaseModel):
    from_profile_id: int
    to_profile_id: int
    message: str = ""


@app.get("/api/health")
def health():
    return {"ok": True, "llm": {n: bool(_llm(n)) for n in
                                ("generate_report", "moderate_review", "verify_proof", "match_roommates")}}


@app.get("/api/report")
def report(address: str = Query(..., min_length=3), lang: str = "en"):
    lang = lang if lang in LANGS else "en"
    try:
        building = data.get_building(address)
    except data.AddressNotFound as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    comm = community.get_community(building["blklot"])

    fn = _llm("generate_report")
    rep = None
    if fn:
        try:
            rep = fn(building, comm["reviews"], lang)
        except Exception as e:
            print(f"[main] generate_report a échoué : {e}")
    if not rep:
        rep = fallback_report(building, comm["reviews"], lang)
    return {"building": building, "report": rep, "community": comm}


@app.get("/api/community")
def community_only(blklot: str):
    """Rafraîchir les avis après un POST sans relancer tout le rapport."""
    return community.get_community(blklot)


@app.post("/api/reviews")
async def post_review(review: str = Form(...), proof: UploadFile | None = File(None)):
    try:
        r = json.loads(review)
    except json.JSONDecodeError:
        raise HTTPException(400, "champ 'review' : JSON invalide")

    verified, reason_v = False, ""
    if proof is not None and proof.filename:
        content = await proof.read()          # en mémoire uniquement, jamais écrit sur disque
        if len(content) > MAX_PROOF_BYTES:
            raise HTTPException(413, "justificatif trop lourd (10 Mo max)")
        expected = data.address_for_blklot(r.get("blklot", "")) or ""
        fn = _llm("verify_proof")
        if fn:
            try:
                res = fn(content, proof.content_type or "application/octet-stream", expected)
                verified, reason_v = bool(res.get("verified")), res.get("reason", "")
            except Exception as e:
                reason_v = f"vérification impossible : {e}"
        else:
            reason_v = "vérification indisponible"
        del content

    fn = _llm("moderate_review")
    try:
        mod = fn(r.get("text", "")) if fn else fallback_moderation(r.get("text", ""))
    except Exception as e:
        print(f"[main] moderate_review a échoué : {e}")
        mod = fallback_moderation(r.get("text", ""))

    if not mod.get("ok"):
        return {"status": "rejected", "verified": verified, "reason": mod.get("reason", "")}
    try:
        community.add_review({**r, "text": mod.get("cleaned_text") or r.get("text", "")},
                             verified=verified)
    except community.ValidationError as e:
        raise HTTPException(422, str(e))
    return {"status": "published", "verified": verified,
            "reason": reason_v or mod.get("reason", "")}


@app.post("/api/profiles")
def post_profile(profile: dict):
    try:
        return community.add_profile(profile)
    except community.ValidationError as e:
        raise HTTPException(422, str(e))


@app.get("/api/roommates")
def roommates(profile_id: int):
    seeker = community.get_profile(profile_id)
    if not seeker:
        raise HTTPException(404, "profil inconnu")
    candidates = community.list_candidates(profile_id)
    fn = _llm("match_roommates")
    try:
        return fn(seeker, candidates) if fn else fallback_match(seeker, candidates)
    except Exception as e:
        print(f"[main] match_roommates a échoué : {e}")
        return fallback_match(seeker, candidates)


@app.post("/api/contact")
def contact(c: ContactIn):
    try:
        return {"ok": community.add_contact_request(c.from_profile_id, c.to_profile_id, c.message)}
    except community.ValidationError as e:
        raise HTTPException(422, str(e))


# sert le front (Personne 3) — à monter en dernier
os.makedirs(os.path.join(HERE, "static"), exist_ok=True)
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
