# Know Your Building (SF)

Entre une adresse de San Francisco, obtiens un rapport généré par Mistral (évictions, plaintes/infractions DBI, permis, rôle fiscal, pompiers, 311). Option : import du bail, analyse OCR des clauses vs. règles de contrôle des loyers de SF.

## Organisation
- `backend/`  : données (geocoding, DataSF, agrégation) -> `GET /api/building`
- `llm/`      : synthèse Mistral + OCR/analyse du bail -> `POST /api/summary`, `POST /api/lease`
- `frontend/` : UI
- `mocks/`    : exemples JSON du contrat, pour travailler en parallèle
- `contract.md` : le contrat d'interface (à modifier à plusieurs, en prévenant)

## Règles
- Chacun reste dans son dossier, une branche par feature, PR vers `main`.
- Ne jamais commiter `.env` (copier `.env.example`).
- Données : https://data.sf.gov (Socrata, ex data.sfgov.org)

## Lancer
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env          # mettre MISTRAL_API_KEY
    uvicorn backend.main:app --reload --port 8000     # depuis la racine du repo

Docs API interactives : http://localhost:8000/docs · état des branchements : `curl localhost:8000/api/health`
Le front est servi sur http://localhost:8000/ dès que `frontend/index.html` existe.

## Backend (Personne 1) — routes de contract.md
    GET  /api/building?address=907%20Valencia%20St   -> format mocks/building.json
    POST /api/summary   body = sortie de /api/building -> format mocks/summary.json
    POST /api/lease     multipart, champ "file"         -> format mocks/lease.json
Exemple réel de /api/building (avec `neighborhood_benchmark` Elastic) : `mocks/building_907_valencia.json`.
Champs en plus du mock (ignorables) : `address.blklot`, `address.neighborhood`,
`property.likely_rent_controlled`, `evictions.scope = "street_block"`.

**Pour `llm/` (Personne 2)** : le backend appelle `summarize(building: dict) -> dict` et
`analyze_lease(file_bytes: bytes, mime: str) -> dict`, cherchées dans `llm/__init__.py`,
`llm/llm.py`, `llm/summary.py`, `llm/lease.py` ou `llm/main.py`. Tant qu'elles n'existent pas :
résumé calculé par règles, et `mocks/lease.json` pour le bail.
SDK : l'équipe utilise `mistralai` 3.7 → importer avec `from mistralai.client import Mistral`
(l'ancien `from mistralai import Mistral` plante en 3.x).

## Elastic (sponsor) — `backend/search.py`
Elasticsearch indexe ~3 ans de plaintes et d'infractions DBI de toute la ville (~72 000 documents
géolocalisés) et ajoute au backend :
- **Comparaison avec les voisins** (agrégations géo) : `/api/building` renvoie en plus
  `neighborhood_benchmark` — ex. 907 Valencia St a plus de plaintes que 99 % des 319 immeubles
  à 500 m (27 contre une médiane de 2). Le résumé l'utilise comme signal « Pire que ses voisins ».
- `GET /api/benchmark?address=...&radius_m=500`
- `GET /api/search?q=no heat&address=...&radius_m=800&open_only=true` : recherche plein texte
  dans toute la ville, résultats groupés par immeuble.
- `semantic=true` : recherche sémantique multilingue (embeddings `mistral-embed` en
  `dense_vector` + kNN hybride) — « moisissure » en français retrouve « mold ».

Mise en place (5 min) :
1. Un cluster : Elastic Cloud (essai gratuit / crédits du hackathon → copier l'URL du
   endpoint Elasticsearch et créer une API key), ou en local :
   `docker run -d -p 9200:9200 -e discovery.type=single-node -e xpack.security.enabled=false docker.elastic.co/elasticsearch/elasticsearch:8.15.3`
2. `.env` : `ELASTIC_URL=...` (+ `ELASTIC_API_KEY=...` pour le cloud)
3. `python backend/search.py ingest --years 3` (~40 s) — ajouter `--embed` pour la recherche
   sémantique (appels Mistral, plus long).
4. `curl localhost:8000/api/health` → `"elastic": {"docs": 71763, ...}`

Cluster Elastic Cloud en 9.x : remplacer la ligne du requirements par `elasticsearch>=9,<10`.
Sans Elastic configuré, tout le reste du backend fonctionne normalement.

## Extension « gens de confiance » (avis, locataires, colocation)
    GET  /api/report?address=&lang=en|es|zh|fr   -> {building, report, community}
    GET  /api/community?blklot=3609042
    POST /api/reviews   multipart : review=<JSON>, proof=<fichier optionnel, jamais stocké>
    POST /api/profiles  ·  GET /api/roommates?profile_id=  ·  POST /api/contact
Données de démo chargées depuis `seed/seed_community.json` au premier lancement
(supprimer `community.db` pour recharger).

## Adresses de démo (données réelles, trouvées par `python backend/find_demo_addresses.py`)
| Rôle          | Adresse              | blklot   | Points clés |
|---------------|----------------------|----------|-------------|
| Problématique | 907 Valencia St      | 3609042  | 17 plaintes DBI ouvertes, 25 dossiers d'infraction, 33 avis d'éviction sur le bloc |
| Bien tenu     | 2288 Broadway        | 0564C104 | 30 logements, 0 plainte en 10 ans |
| Évictions     | 3033 Cesar Chavez St | 5505019  | 9 évictions Ellis Act / owner move-in sur le bloc en 5 ans |

## À savoir sur les données
- Évictions et loyers sont anonymisés par la ville au **bloc de rue** ("900 Block Of Valencia Street") : ne jamais dire « cet immeuble ».
- Une adresse absente de la base de la ville est rattachée au numéro le plus proche de la même rue (`address.normalized` l'indique).
- Cache mémoire par adresse : ouvrir les adresses de démo une fois avant de passer devant le jury.
