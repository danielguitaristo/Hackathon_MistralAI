"""
backend/search.py — Personne 1 · intégration Elastic (sponsor)

Elasticsearch indexe ~3 ans de plaintes et d'infractions DBI de toute la ville
(~80 000 documents géolocalisés) pour trois usages :
  1. benchmark(...)  : comparer un immeuble à ses voisins dans un rayon (agrégations geo)
                       -> "plus de plaintes que 92 % des immeubles à 500 m"
  2. search(...)     : recherche plein texte + géo dans toute la ville
                       -> "moisissure près de Dolores Park, plaintes ouvertes"
  3. semantic=True   : recherche sémantique multilingue (embeddings mistral-embed en
                       dense_vector + kNN) : "moisissure" en français retrouve "mold"

Configuration (.env) :
  ELASTIC_URL=https://xxx.es.us-west-1.aws.found.io:443   (ou http://localhost:9200)
  ELASTIC_API_KEY=...            (vide en local)
  # ou ELASTIC_CLOUD_ID=... + ELASTIC_API_KEY=...

Remplir l'index (une fois) :
  python backend/search.py ingest --years 3            # ~2-4 min
  python backend/search.py ingest --years 3 --embed    # + embeddings Mistral (plus long)
Tester :
  python backend/search.py status
  python backend/search.py search "mold" --address "907 Valencia St"
  python backend/search.py benchmark --address "907 Valencia St"
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

import data  # noqa: E402

INDEX = os.getenv("ELASTIC_INDEX", "kyb-issues")
EMBED_MODEL = "mistral-embed"
EMBED_DIMS = 1024

_client = None


# ---------------------------------------------------------------- connexion
def es():
    """Client Elasticsearch, ou None si non configuré / injoignable (le backend continue sans)."""
    global _client
    if _client is not None:
        return _client or None
    url, cloud_id = os.getenv("ELASTIC_URL"), os.getenv("ELASTIC_CLOUD_ID")
    api_key = os.getenv("ELASTIC_API_KEY") or None
    if not (url or cloud_id):
        _client = False
        return None
    try:
        from elasticsearch import Elasticsearch
        kw = {"api_key": api_key, "request_timeout": 15}
        _client = Elasticsearch(cloud_id=cloud_id, **kw) if cloud_id else Elasticsearch(url, **kw)
        _client.info()
    except Exception as e:
        print(f"[search] Elasticsearch indisponible : {e}")
        _client = False
    return _client or None


def status() -> dict:
    c = es()
    if not c:
        return {"configured": bool(os.getenv("ELASTIC_URL") or os.getenv("ELASTIC_CLOUD_ID")),
                "reachable": False, "docs": 0, "has_embeddings": False}
    try:
        if not c.indices.exists(index=INDEX):
            return {"configured": True, "reachable": True, "docs": 0, "has_embeddings": False}
        n = c.count(index=INDEX)["count"]
        emb = c.count(index=INDEX, query={"exists": {"field": "embedding"}})["count"]
        return {"configured": True, "reachable": True, "docs": n, "has_embeddings": emb > 0}
    except Exception as e:
        return {"configured": True, "reachable": False, "error": str(e), "docs": 0,
                "has_embeddings": False}


def available() -> bool:
    return status()["docs"] > 0


# ---------------------------------------------------------------- embeddings Mistral
def _mistral_client():
    try:                                   # mistralai >= 3 (version de l'équipe : 3.7)
        from mistralai.client import Mistral
    except ImportError:                    # mistralai 1.x / 2.x
        from mistralai import Mistral
    return Mistral(api_key=os.environ["MISTRAL_API_KEY"])


def embed(texts: list[str]) -> list[list[float]]:
    client = _mistral_client()
    out = []
    for i in range(0, len(texts), 64):
        resp = client.embeddings.create(model=EMBED_MODEL, inputs=[t[:2000] for t in texts[i:i + 64]])
        out.extend(d.embedding for d in resp.data)
    return out


# ---------------------------------------------------------------- ingestion
MAPPING = {
    "properties": {
        "source": {"type": "keyword"},          # dbi_complaint | dbi_violation
        "complaint_number": {"type": "keyword"},
        "date": {"type": "date"},
        "text": {"type": "text", "analyzer": "english"},
        "status": {"type": "keyword"},          # open | closed
        "unsafe_building": {"type": "boolean"},
        "blklot": {"type": "keyword"},
        "address": {"type": "keyword"},
        "neighborhood": {"type": "keyword"},
        "location": {"type": "geo_point"},
        "embedding": {"type": "dense_vector", "dims": EMBED_DIMS, "index": True,
                      "similarity": "cosine"},
    }
}


def _paged(dataset: str, where: str, select: str, page: int = 20000):
    offset = 0
    while True:
        rows = data.soql(dataset, select=select, where=where, order=":id",
                         limit=page, offset=offset)
        yield from rows
        if len(rows) < page:
            break
        offset += page


def _addr(r):
    return " ".join(x for x in (r.get("street_number"), (r.get("street_name") or "").upper(),
                                (r.get("street_suffix") or "").upper()) if x)


def _point(p):
    if isinstance(p, dict) and p.get("coordinates"):
        lon, lat = p["coordinates"][:2]
        return {"lat": float(lat), "lon": float(lon)}
    return None


def _docs(years: int):
    since = data.years_ago(years)
    sel = ("complaint_number,date_filed,complaint_description,status,block,lot,"
           "street_number,street_name,street_suffix,analysis_neighborhood,point")
    for r in _paged("dbi_complaints", f"date_filed >= '{since}' AND point IS NOT NULL", sel):
        loc, text = _point(r.get("point")), (r.get("complaint_description") or "").strip()
        if not loc or not text or not r.get("block"):
            continue
        yield {"_id": f"c-{r['complaint_number']}", "source": "dbi_complaint",
               "complaint_number": r["complaint_number"], "date": data.d10(r.get("date_filed")),
               "text": text, "status": "open" if r.get("status") == "Active" else "closed",
               "unsafe_building": False, "blklot": f"{r['block']}{r.get('lot', '')}",
               "address": _addr(r), "neighborhood": r.get("analysis_neighborhood"),
               "location": loc}

    sel = ("complaint_number,item_sequence_number,date_filed,item,nov_item_description,"
           "status,block,lot,street_number,street_name,street_suffix,"
           "neighborhoods_analysis_boundaries,unsafe_building,location")
    for r in _paged("violations", f"date_filed >= '{since}' AND location IS NOT NULL", sel):
        loc = _point(r.get("location"))
        item = (r.get("item") or "").strip()
        detail = (r.get("nov_item_description") or "").strip()
        if item == "inspector comments" or item.startswith("this notice includes"):
            item = ""
        if detail.lower().startswith(data.BOILERPLATE):
            detail = ""
        text = " — ".join(x for x in (item, detail[:500]) if x)
        if not loc or not text or not r.get("block"):
            continue
        yield {"_id": f"v-{r['complaint_number']}-{r.get('item_sequence_number', '')}",
               "source": "dbi_violation", "complaint_number": r["complaint_number"],
               "date": data.d10(r.get("date_filed")), "text": text,
               "status": "open" if (r.get("status") or "").lower() == "active" else "closed",
               "unsafe_building": r.get("unsafe_building") == "Y",
               "blklot": f"{r['block']}{r.get('lot', '')}", "address": _addr(r),
               "neighborhood": r.get("neighborhoods_analysis_boundaries"), "location": loc}


def ingest(years: int = 3, with_embeddings: bool = False, recreate: bool = True) -> int:
    from elasticsearch import helpers
    c = es()
    if not c:
        raise RuntimeError("Elasticsearch non configuré : renseigner ELASTIC_URL dans .env")
    if recreate and c.indices.exists(index=INDEX):
        c.indices.delete(index=INDEX)
    if not c.indices.exists(index=INDEX):
        c.indices.create(index=INDEX, mappings=MAPPING)

    batch, total = [], 0

    def flush():
        nonlocal batch, total
        if with_embeddings:
            for d, v in zip(batch, embed([d["text"] for d in batch])):
                d["embedding"] = v
        helpers.bulk(c, ({"_index": INDEX, **d} for d in batch))
        total += len(batch)
        print(f"[search] {total} documents indexés")
        batch = []

    for d in _docs(years):
        batch.append(d)
        if len(batch) >= 2000:
            flush()
    if batch:
        flush()
    c.indices.refresh(index=INDEX)
    return total


# ---------------------------------------------------------------- requêtes
def _geo(lat, lon, radius_m):
    return {"geo_distance": {"distance": f"{int(radius_m)}m", "location": {"lat": lat, "lon": lon}}}


def benchmark(lat: float, lon: float, blklot: str, radius_m: int = 500, years: int = 3) -> dict | None:
    """Classe l'immeuble parmi les immeubles voisins ayant au moins une plainte DBI."""
    c = es()
    if not c:
        return None
    since = data.years_ago(years)
    res = c.search(index=INDEX, size=0, query={"bool": {"filter": [
        {"term": {"source": "dbi_complaint"}},
        {"range": {"date": {"gte": since}}},
        _geo(lat, lon, radius_m)]}},
        aggs={"by_building": {"terms": {"field": "blklot", "size": 5000},
                              "aggs": {"addr": {"terms": {"field": "address", "size": 1}},
                                       "open": {"filter": {"term": {"status": "open"}}}}}})
    buckets = res["aggregations"]["by_building"]["buckets"]
    counts = sorted(b["doc_count"] for b in buckets)
    if not counts:
        return {"radius_m": radius_m, "years": years, "buildings_compared": 0}
    mine = next((b["doc_count"] for b in buckets if b["key"] == blklot), 0)
    below = sum(1 for x in counts if x < mine)
    top = sorted(buckets, key=lambda b: -b["doc_count"])[:5]
    return {
        "radius_m": radius_m,
        "years": years,
        "scope": "immeubles avec au moins une plainte DBI dans le rayon",
        "buildings_compared": len(counts),
        "this_building_complaints": mine,
        "percentile": round(100 * below / len(counts)),   # % d'immeubles voisins avec moins de plaintes
        "median_neighbor": counts[len(counts) // 2],
        "worst_neighbors": [{"blklot": b["key"],
                             "address": (b["addr"]["buckets"] or [{"key": None}])[0]["key"],
                             "complaints": b["doc_count"], "open": b["open"]["doc_count"]}
                            for b in top],
    }


def search(q: str, lat: float | None = None, lon: float | None = None, radius_m: int = 1000,
           open_only: bool = False, semantic: bool = False, years: int = 3, size: int = 10) -> dict:
    """Recherche plein texte (ou sémantique) groupée par immeuble."""
    c = es()
    if not c:
        return {"error": "Elasticsearch indisponible", "buildings": []}
    filters = [{"range": {"date": {"gte": data.years_ago(years)}}}]
    if lat is not None and lon is not None:
        filters.append(_geo(lat, lon, radius_m))
    if open_only:
        filters.append({"term": {"status": "open"}})

    body = {"size": 200, "_source": {"excludes": ["embedding"]},
            "query": {"bool": {"must": [{"match": {"text": {"query": q, "fuzziness": "AUTO"}}}],
                               "filter": filters}}}
    mode = "full_text"
    if semantic and status()["has_embeddings"] and os.getenv("MISTRAL_API_KEY"):
        vec = embed([q])[0]
        body = {"size": 200, "_source": {"excludes": ["embedding"]},
                "knn": {"field": "embedding", "query_vector": vec, "k": 200,
                        "num_candidates": 1000, "filter": filters},
                # hybride : le plein texte s'ajoute au score kNN quand les mots correspondent
                "query": {"bool": {"should": [{"match": {"text": {"query": q, "boost": 0.2}}}],
                                   "filter": filters}}}
        mode = "semantic_hybrid"

    hits = c.search(index=INDEX, **body)["hits"]["hits"]
    groups: dict[str, dict] = {}
    for h in hits:
        s = h["_source"]
        g = groups.setdefault(s["blklot"], {
            "blklot": s["blklot"], "address": s.get("address"), "neighborhood": s.get("neighborhood"),
            "lat": s["location"]["lat"], "lon": s["location"]["lon"],
            "score": h["_score"] or 0, "matches": 0, "open": 0, "examples": []})
        g["matches"] += 1
        g["open"] += s["status"] == "open"
        if len(g["examples"]) < 3:
            g["examples"].append({k: s.get(k) for k in ("date", "text", "status", "source")})
    ranked = sorted(groups.values(), key=lambda g: (-g["score"], -g["matches"]))[:size]
    return {"query": q, "mode": mode, "total_hits": len(hits), "buildings": ranked}


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("--years", type=int, default=3)
    p.add_argument("--embed", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("search"); p.add_argument("q"); p.add_argument("--address")
    p.add_argument("--radius", type=int, default=1000); p.add_argument("--open", action="store_true")
    p.add_argument("--semantic", action="store_true")
    p = sub.add_parser("benchmark"); p.add_argument("--address", required=True)
    p.add_argument("--radius", type=int, default=500)
    a = ap.parse_args()

    t0 = datetime.now(timezone.utc)
    if a.cmd == "ingest":
        n = ingest(a.years, a.embed)
        print(f"OK : {n} documents en {(datetime.now(timezone.utc) - t0).seconds}s")
    elif a.cmd == "status":
        print(json.dumps(status(), indent=2))
    elif a.cmd == "search":
        lat = lon = None
        if a.address:
            loc = data.locate(a.address); lat, lon = loc["lat"], loc["lon"]
        print(json.dumps(search(a.q, lat, lon, a.radius, a.open, a.semantic), indent=2,
                         ensure_ascii=False)[:3000])
    elif a.cmd == "benchmark":
        loc = data.locate(a.address)
        print(json.dumps(benchmark(loc["lat"], loc["lon"], loc["blklot"], a.radius), indent=2,
                         ensure_ascii=False))
