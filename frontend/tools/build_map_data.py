#!/usr/bin/env python3
"""Build the static geometry behind the map, in frontend/map-data/.

The map colors neighborhoods and assessor blocks. DataSF has no block outlines, so
this script derives one from the parcels of each block (convex hull, a few vertices).
It also stores a snapshot of the citywide counts, so the map paints immediately;
the page then replaces them with live numbers from DataSF.

Only needs curl. Run it from the repo root when parcels change (takes a few minutes):
    python3 frontend/tools/build_map_data.py
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
from datetime import date, timedelta

BASE = "https://data.sf.gov/resource/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "map-data")
PAGE = 20000
ORIGIN = (-122.6, 37.6)     # block coordinates are stored as integers from this point
SCALE = 100000              # 1e-5 degree, about 1 m
MAX_VERTICES = 8
PLACEHOLDER_BLOCKS = {"VACST", "PARK", "9900"}   # citywide parcel groups, not real blocks
SINCE = (date.today() - timedelta(days=3650)).isoformat()    # same window as backend/data.py


def soql(dataset: str, **params) -> list[dict]:
    url = BASE + dataset + ".json?" + urllib.parse.urlencode({"$" + k: v for k, v in params.items()})
    for attempt in range(4):
        path = tempfile.mktemp()
        done = subprocess.run(["curl", "-sS", "-m", "180", "-o", path, "-w", "%{http_code}", url],
                              capture_output=True, text=True)
        if done.returncode == 0 and done.stdout == "200":
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            os.remove(path)
            return rows
        print(f"  retry {attempt + 1}: {done.stdout} {done.stderr[:120]}", flush=True)
        if os.path.exists(path):
            os.remove(path)
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"DataSF did not answer: {url[:200]}")


def vertices(geometry: dict):
    def walk(node):
        if isinstance(node[0], (int, float)):
            yield (node[0], node[1])
        else:
            for child in node:
                yield from walk(child)
    return walk(geometry["coordinates"])


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(reversed(pts))


def triangle_area(a, b, c) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2


def polygon_area(ring) -> float:
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring)))) / 2


def simplify(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop the least useful vertices (Visvalingam) down to MAX_VERTICES."""
    ring = list(ring)
    while len(ring) > MAX_VERTICES:
        n = len(ring)
        areas = [triangle_area(ring[i - 1], ring[i], ring[(i + 1) % n]) for i in range(n)]
        ring.pop(areas.index(min(areas)))
    return ring


def build_neighborhoods() -> list[str]:
    rows = soql("j2bu-swwd", select="nhood,simplify(the_geom,0.0002) as the_geom", limit=100)
    features = []
    for row in rows:
        geometry = row["the_geom"]

        def rounded(node):
            if isinstance(node[0], (int, float)):
                return [round(node[0], 5), round(node[1], 5)]
            return [rounded(child) for child in node]

        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        largest = max((p[0] for p in polygons), key=polygon_area)
        features.append({
            "type": "Feature",
            "properties": {
                "name": row["nhood"],
                "lx": round(sum(p[0] for p in largest) / len(largest), 5),
                "ly": round(sum(p[1] for p in largest) / len(largest), 5),
            },
            "geometry": {"type": geometry["type"], "coordinates": rounded(geometry["coordinates"])},
        })
    features.sort(key=lambda f: f["properties"]["name"])
    with open(os.path.join(OUT, "neighborhoods.json"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"), ensure_ascii=False)
    print(f"neighborhoods: {len(features)}", flush=True)
    return [f["properties"]["name"] for f in features]


def build_blocks(names: list[str]) -> None:
    points: dict[str, list] = collections.defaultdict(list)
    hoods: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    offset = 0
    while True:
        rows = soql("acdm-wktn", select="block_num,analysis_neighborhood,shape",
                    where="active=true", group="block_num,analysis_neighborhood,shape",
                    order="block_num", limit=PAGE, offset=offset)
        for row in rows:
            if row.get("shape") and row.get("block_num"):
                points[row["block_num"]].extend(vertices(row["shape"]))
                if row.get("analysis_neighborhood"):
                    hoods[row["block_num"]][row["analysis_neighborhood"]] += 1
        offset += len(rows)
        print(f"footprints: {offset}, blocks: {len(points)}", flush=True)
        if len(rows) < PAGE:
            break

    complaints = {r["block"]: int(r["n"]) for r in soql(
        "gm2e-bten", select="block,count(*) as n", where=f"date_filed>='{SINCE}'", group="block", limit=50000)}
    violations = {r["block"]: int(r["n"]) for r in soql(
        "nbtm-fbw5", select="block,count(distinct complaint_number) as n", where=f"date_filed>='{SINCE}'",
        group="block", limit=50000)}

    index = {name: i for i, name in enumerate(names)}
    blocks = []
    for block, pts in points.items():
        ring = simplify(convex_hull(pts))
        if len(ring) < 3 or block in PLACEHOLDER_BLOCKS:
            continue
        hood = hoods[block].most_common(1)[0][0] if hoods[block] else None
        flat = []
        for lon, lat in ring:
            flat += [round((lon - ORIGIN[0]) * SCALE), round((lat - ORIGIN[1]) * SCALE)]
        blocks.append((polygon_area(ring), [block, index.get(hood, -1), complaints.get(block, 0),
                                            violations.get(block, 0), flat]))
    blocks.sort(key=lambda b: -b[0])     # large blocks first, so small ones draw on top
    data = {"asOf": date.today().isoformat(), "since": SINCE, "origin": ORIGIN, "scale": SCALE,
            "neighborhoods": names, "blocks": [b[1] for b in blocks]}
    with open(os.path.join(OUT, "blocks.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    print(f"blocks: {len(blocks)}", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    build_blocks(build_neighborhoods())
