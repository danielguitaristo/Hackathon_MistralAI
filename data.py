"""
data.py — Personne 1
get_building(address) -> BuildingData (cf. contrat commun)

Chaîne : adresse -> EAS (lat/lon) -> Parcels (block/lot) -> requêtes parallèles.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

BASE = "https://data.sf.gov/resource"
TIMEOUT = 10
MAX_ITEMS = 30

DS = {
    "addresses": "ramy-di5m",
    "parcels": "acdm-wktn",
    "tax_rolls": "wv5m-vpq2",
    "dbi_complaints": "gm2e-bten",
    "violations": "nbtm-fbw5",
    "permits": "i98e-djp9",
    "evictions": "5cei-gny5",
    "rent_inventory": "gdc7-dmcn",
    "311": "vw6y-z8j6",
}
SOURCES = {k: f"https://data.sf.gov/d/{v}" for k, v in DS.items()}

EVICTION_REASONS = [
    "non_payment", "breach", "nuisance", "illegal_use", "failure_to_sign_renewal",
    "access_denial", "unapproved_subtenant", "owner_move_in", "demolition",
    "capital_improvement", "substantial_rehab", "ellis_act_withdrawal",
    "condo_conversion", "roommate_same_unit", "other_cause", "late_payments",
    "lead_remediation", "development", "good_samaritan_ends",
]

SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "PLACE": "PL", "COURT": "CT", "LANE": "LN", "TERRACE": "TER",
    "HIGHWAY": "HWY", "WAY": "WAY", "ALLEY": "ALY", "CIRCLE": "CIR", "PLAZA": "PLZ",
}
SUFFIX_TOKENS = set(SUFFIXES) | set(SUFFIXES.values())

BOILERPLATE = ("due to the violations noted", "it is the property owner",
               "this notice includes", "***correction")

_session = requests.Session()
if os.getenv("SOCRATA_APP_TOKEN"):
    _session.headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]

_cache: dict[str, dict] = {}
_blklot_to_address: dict[str, str] = {}


class AddressNotFound(Exception):
    pass


# ---------------------------------------------------------------- utilitaires
def soql(dataset_key: str, **params) -> list[dict]:
    """Requête SoQL. Lève en cas d'erreur (géré par safe())."""
    p = {f"${k}": v for k, v in params.items()}
    last = None
    for _ in range(2):  # 1 retry : l'API DataSF a des lenteurs ponctuelles
        try:
            r = _session.get(f"{BASE}/{DS[dataset_key]}.json", params=p, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
    raise last


def safe(fn, default):
    try:
        return fn()
    except Exception as e:  # une source en panne ne casse pas la fiche
        print(f"[data] source en erreur : {fn.__name__ if hasattr(fn, '__name__') else fn}: {e}")
        return default


def q(s: str) -> str:
    """Échappe une valeur texte pour SoQL."""
    return s.replace("'", "''")


def d10(v) -> str | None:
    return v[:10] if isinstance(v, str) and len(v) >= 10 else None


def to_int(v):
    try:
        i = int(float(v))
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def years_ago(n: int) -> str:
    return (date.today() - timedelta(days=365 * n)).isoformat()


def normalize_address(address: str) -> tuple[str, str, str | None]:
    """'3527 Judah Street, Apt 2, San Francisco, CA' -> ('3527', 'JUDAH', 'ST')"""
    a = address.upper()
    a = re.sub(r",.*$", "", a)                       # coupe ville / état / unité après virgule
    a = re.sub(r"\b(APT|UNIT|STE|SUITE)\b.*$", "", a)
    a = re.sub(r"#.*$", "", a)
    a = re.sub(r"\bSAN FRANCISCO\b.*$", "", a)
    a = re.sub(r"[^\w\s]", " ", a)
    tokens = a.split()
    if len(tokens) < 2 or not re.match(r"^\d+[A-Z]?$", tokens[0]):
        raise AddressNotFound("Format attendu : '<numéro> <rue>', ex. '777 Bay St'")
    number = re.match(r"^\d+", tokens[0]).group()
    rest = tokens[1:]
    suffix = None
    if rest and rest[-1] in SUFFIX_TOKENS:
        suffix = SUFFIXES.get(rest[-1], rest[-1])
        rest = rest[:-1]
    # "24th" / "3rd" restent tels quels ; "Twenty Fourth" non géré (hackathon)
    street = " ".join(rest)
    return number, street, suffix


# ---------------------------------------------------------------- géolocalisation
def locate(address: str) -> dict:
    number, street, suffix = normalize_address(address)
    where = f"address_number='{q(number)}' AND street_name='{q(street)}'"
    if suffix:
        rows = soql("addresses", where=where + f" AND street_type='{q(suffix)}'", limit=50)
        if not rows:
            rows = soql("addresses", where=where, limit=50)
    else:
        rows = soql("addresses", where=where, limit=50)
    if not rows:  # repli LIKE
        rows = soql("addresses", where=f"upper(address) like '{q(number)} {q(street)}%'", limit=50)
    if not rows:  # repli : numéro le plus proche sur la même rue, même centaine
        hundred = (int(number) // 100) * 100
        cands = soql("addresses",
                     where=f"street_name='{q(street)}' AND address_number "
                           f"between '{hundred}' and '{hundred + 99}'", limit=200)
        cands = [c for c in cands if str(c.get("address_number", "")).isdigit()
                 and hundred <= int(c["address_number"]) <= hundred + 99]
        if cands:
            best = min(cands, key=lambda c: abs(int(c["address_number"]) - int(number)))
            rows = [c for c in cands if c["address_number"] == best["address_number"]]
            number = best["address_number"]
    if not rows:
        raise AddressNotFound(f"Adresse introuvable à San Francisco : {address}")

    row = next((r for r in rows if not r.get("unit_number")), rows[0])
    lat, lon = float(row["latitude"]), float(row["longitude"])
    base_addr = re.sub(r"\s*#.*$", "", row["address"])

    parcels = soql(
        "parcels",
        select="blklot,block_num,lot_num,analysis_neighborhood,supervisor_district",
        where=f"intersects(shape,'POINT({lon} {lat})') AND active=true",
        limit=50,
    )
    if not parcels:
        raise AddressNotFound(f"Parcelle introuvable pour : {base_addr}")
    p = parcels[0]
    return {
        "number": number,
        "street": street,
        "matched_address": base_addr,
        "lat": lat,
        "lon": lon,
        "block": p["block_num"],
        "lot": p["lot_num"],
        "lots": sorted({x["lot_num"] for x in parcels}),
        "blklot": p["blklot"],
        "neighborhood": p.get("analysis_neighborhood") or row.get("nhood"),
        "supervisor_district": p.get("supervisor_district") or row.get("supervisor"),
    }


def parcel_filter(loc: dict, number_field: str = "street_number") -> str:
    lots = ",".join(f"'{q(l)}'" for l in loc["lots"])
    return (f"block='{q(loc['block'])}' AND "
            f"(lot in ({lots}) OR {number_field}='{q(loc['number'])}')")


def block_face(loc: dict) -> str:
    """3527 JUDAH -> '3500 BLOCK OF JUDAH' (format évictions / rent board)"""
    hundred = (int(loc["number"]) // 100) * 100
    return f"{hundred} BLOCK OF {loc['street']}"


# ---------------------------------------------------------------- sources
def fetch_profile(loc):
    lots = ",".join(f"'{q(l)}'" for l in loc["lots"])
    rows = soql("tax_rolls",
                where=f"block='{q(loc['block'])}' AND lot in ({lots})",
                order="closed_roll_year DESC", limit=len(loc["lots"]) * 3 or 3)
    if not rows:
        return {"year_built": None, "units": None, "stories": None,
                "last_sale_date": None, "likely_rent_controlled": None}
    latest = rows[0]["closed_roll_year"]
    rows = [r for r in rows if r["closed_roll_year"] == latest]
    years = [to_int(r.get("year_property_built")) for r in rows]
    years = [y for y in years if y and y > 1800]
    units = sum(to_int(r.get("number_of_units")) or 0 for r in rows) or None
    stories = max((to_int(r.get("number_of_stories")) or 0 for r in rows), default=0) or None
    sales = sorted(filter(None, (d10(r.get("current_sales_date")) for r in rows)), reverse=True)
    year_built = min(years) if years else None
    rc = (year_built < 1979 and units >= 2) if (year_built and units) else None
    return {"year_built": year_built, "units": units, "stories": stories,
            "last_sale_date": sales[0] if sales else None, "likely_rent_controlled": rc}


def fetch_complaints(loc):
    rows = soql("dbi_complaints",
                where=parcel_filter(loc) + f" AND date_filed >= '{years_ago(10)}'",
                order="date_filed DESC", limit=500)
    items = [{
        "date": d10(r.get("date_filed")),
        "description": (r.get("complaint_description") or "").strip(),
        "status": "open" if r.get("status") == "Active" else "closed",
        "closed_date": d10(r.get("closed_date")),
    } for r in rows]
    return items, len(items), sum(1 for i in items if i["status"] == "open")


def fetch_violations(loc):
    rows = soql("violations",
                where=parcel_filter(loc) + f" AND date_filed >= '{years_ago(10)}'",
                order="date_filed DESC", limit=500)
    items = []
    for r in rows:
        item = (r.get("item") or "").strip()
        detail = (r.get("nov_item_description") or r.get("code_violation_desc") or "").strip()
        if item == "inspector comments" or item.startswith("this notice includes"):
            item = ""          # texte standard, sans intérêt
        if detail.lower().startswith(BOILERPLATE):
            detail = ""
        desc = " — ".join(x for x in (item, detail[:300]) if x)
        if not desc and r.get("unsafe_building") != "Y":
            continue
        items.append({
            "date": d10(r.get("date_filed")),
            "category": r.get("nov_category_description") or "",
            "description": desc,
            "status": "open" if (r.get("status") or "").lower() == "active" else "closed",
            "unsafe_building": r.get("unsafe_building") == "Y",
        })
    n_complaints = len({r.get("complaint_number") for r in rows})
    return items, n_complaints


def fetch_permits(loc):
    rows = soql("permits",
                where=parcel_filter(loc) + f" AND filed_date >= '{years_ago(10)}'",
                order="filed_date DESC", limit=MAX_ITEMS)
    return [{
        "date": d10(r.get("filed_date")),
        "type": r.get("permit_type_definition") or "",
        "description": (r.get("description") or "").strip(),
        "status": r.get("status") or "",
        "estimated_cost": to_float(r.get("estimated_cost")),
    } for r in rows]


def fetch_evictions(loc):
    rows = soql("evictions",
                where=f"upper(address) like '{q(block_face(loc))} %' "
                      f"AND file_date >= '{years_ago(10)}'",
                order="file_date DESC", limit=500)
    items = [{"date": d10(r.get("file_date")),
              "reasons": [k for k in EVICTION_REASONS if r.get(k) is True]} for r in rows]
    return items, len(items)


def fetch_rents(loc):
    rows = soql("rent_inventory",
                select="bedroom_count,monthly_rent",
                where=f"upper(block_address) like '{q(block_face(loc))} %' "
                      f"AND monthly_rent IS NOT NULL",
                limit=MAX_ITEMS)
    def low(rng):  # "$1-$250" = loyer gratuit / gardien : on écarte
        m = re.match(r"\$(\d+)", rng or "")
        return not m or int(m.group(1)) < 500
    return [{"bedrooms": r.get("bedroom_count"), "rent_range": r.get("monthly_rent")}
            for r in rows if r.get("monthly_rent") and not low(r["monthly_rent"])]


def fetch_311(loc):
    where = (f"within_circle(point,{loc['lat']},{loc['lon']},150) "
             f"AND requested_datetime >= '{years_ago(1)}'")
    rows = soql("311", select="service_name,count(*) as n", where=where,
                group="service_name", order="n DESC", limit=50)
    cats = [{"name": r["service_name"], "count": int(r["n"])} for r in rows if r.get("service_name")]
    return {"total_last_12m": sum(c["count"] for c in cats), "top_categories": cats[:8]}


# ---------------------------------------------------------------- API publique
def get_building(address: str) -> dict:
    key = address.strip().upper()
    if key in _cache:
        return _cache[key]

    loc = locate(address)

    with ThreadPoolExecutor(max_workers=8) as ex:
        f = {
            "profile": ex.submit(safe, lambda: fetch_profile(loc), None),
            "complaints": ex.submit(safe, lambda: fetch_complaints(loc), ([], 0, 0)),
            "violations": ex.submit(safe, lambda: fetch_violations(loc), ([], 0)),
            "permits": ex.submit(safe, lambda: fetch_permits(loc), []),
            "evictions": ex.submit(safe, lambda: fetch_evictions(loc), ([], 0)),
            "rents": ex.submit(safe, lambda: fetch_rents(loc), []),
            "311": ex.submit(safe, lambda: fetch_311(loc),
                             {"total_last_12m": 0, "top_categories": []}),
        }
        res = {k: v.result() for k, v in f.items()}

    complaints, n_compl, n_open = res["complaints"]
    violations, n_viol = res["violations"]
    evictions, n_evict = res["evictions"]
    profile = res["profile"] or {"year_built": None, "units": None, "stories": None,
                                 "last_sale_date": None, "likely_rent_controlled": None}

    building = {
        "query_address": address,
        "matched_address": loc["matched_address"],
        "lat": loc["lat"],
        "lon": loc["lon"],
        "block": loc["block"],
        "lot": loc["lot"],
        "blklot": loc["blklot"],
        "neighborhood": loc["neighborhood"],
        "supervisor_district": loc["supervisor_district"],
        "profile": profile,
        "dbi_complaints": complaints[:MAX_ITEMS],
        "violations": violations[:MAX_ITEMS],
        "permits": res["permits"],
        "block_evictions": evictions[:MAX_ITEMS],
        "block_rents": res["rents"],
        "neighborhood_311": res["311"],
        "stats": {
            "open_complaints": n_open,
            "complaints_10y": n_compl,
            "violations_10y": n_viol,       # nb de dossiers avec infraction(s)
            "evictions_block_10y": n_evict,
        },
        "sources": SOURCES,
    }
    _cache[key] = building
    _blklot_to_address[loc["blklot"]] = loc["matched_address"]
    return building


def address_for_blklot(blklot: str) -> str | None:
    """Adresse d'un bâtiment à partir de son blklot (pour vérifier une preuve)."""
    if blklot in _blklot_to_address:
        return _blklot_to_address[blklot]
    rows = safe(lambda: soql("parcels",
                             select="from_address_num,street_name,street_type",
                             where=f"blklot='{q(blklot)}'", limit=1), [])
    if rows and rows[0].get("from_address_num"):
        r = rows[0]
        addr = f"{r['from_address_num']} {r.get('street_name', '')} {r.get('street_type', '')}".strip()
        _blklot_to_address[blklot] = addr
        return addr
    return None


if __name__ == "__main__":
    import json
    import sys
    import time
    t = time.time()
    b = get_building(sys.argv[1] if len(sys.argv) > 1 else "777 Bay St")
    print(json.dumps({k: v for k, v in b.items() if k != "sources"}, indent=2)[:4000])
    print(f"\n{time.time() - t:.1f}s — stats: {b['stats']}")
