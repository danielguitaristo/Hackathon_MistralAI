# Récap des fonctionnalités pour le front (P3)

**Serveur :** lancer `uvicorn backend.main:app --port 8000` depuis la racine du dépôt.
- Il sert le front depuis **`frontend/index.html`**. Ta branche a `static/index.html` : déplace-le dans `frontend/`, sinon la page n'est pas servie et `API_BASE = ''` ne marche pas.
- Ne merge pas le dossier `.venv/` de ta branche.

## 1. Déjà dans ton front (routes du contrat initial), branché sur l'IA

| Route | Réponse | Temps |
|---|---|---|
| `GET /api/report?address=…&lang=en\|es\|zh\|fr` | `{building, report, community}` (détail ci-dessous) | ~10 s au 1er appel, instantané ensuite (cache) |
| `POST /api/reviews` (multipart : champ `review` = JSON en texte, champ `proof` = fichier facultatif) | `{status: published\|rejected, verified, reason}` | ~1 s ; 3 à 4 s avec justificatif |
| `GET /api/community?blklot=…` | avis à jour après un envoi | instantané |
| `POST /api/profiles`, `GET /api/roommates?profile_id=…`, `POST /api/contact` | matching : `[{profile, score (0 à 1), why}]` | 2 à 3 s |

Contenu de `report` :
- `overall` : `green`, `orange` ou `red` ;
- `signals[]` : chacun a un `level` (mêmes valeurs) et une `source` (une clé de `building.sources`, ou `community_reviews`) ;
- `tenant_voice` ;
- `questions_to_ask_landlord` ;
- `disclaimer` (toujours présent).

## 2. Nouveau côté P1 (contract.md), pas encore dans ton front

| Route | Réponse | Temps |
|---|---|---|
| `POST /api/lease` (multipart, champ `file`) | `{clauses: [{text, issue, severity: high\|medium\|low, rule_ref}]}` ; en cas d'échec : `{clauses: [], error}` | ~7 s |
| `GET /api/building?address=…` puis `POST /api/summary` (body = le building) | `{risk_score (0 à 100), risk_level: low\|medium\|high, headline, summary, red_flags: [{title, detail, source}]}`, avec `source` = `evictions`, `dbi`, `permits`, `fire` ou `311` | 4 à 5 s, plus le temps DataSF |
| `GET /api/benchmark?address=…` (Elastic) | percentile, médiane des voisins, `worst_neighbors` | ~1 s |
| `GET /api/search?q=…&address=…&semantic=true` (Elastic) | plaintes et infractions groupées par immeuble | ~1 s |
| `GET /api/health` | liste des fonctions IA actives | instantané |

## 3. Une seule chose à ajouter (temps limité) : l'analyse du bail

- Un champ fichier qui envoie à `POST /api/lease`.
- La liste des clauses, colorées par gravité, avec leur `rule_ref`.
- La mention « Not legal advice ».

Pour la démo : uploader `mocks/demo_lease_bad_clauses.pdf`. L'analyse relève 8 clauses illégales : dépôt de 3 mois, entrée sans préavis, renonciation aux recours…

## 4. Règles d'affichage (non négociables)

- Évictions : toujours « on this block », jamais « this building ».
- Données officielles et texte de l'IA visuellement séparés. Un avis n'est jamais présenté comme un fait.
- Mention « Not legal advice » sous le rapport et sous l'analyse du bail.
- Avis de démo (`demo: true`) affichés comme « fictional example ».
- Aucun email ni téléphone affiché.

## 5. Fichiers de démo (`mocks/`)

- `demo_lease_907_valencia.pdf` (et ses équivalents `2288_broadway`, `3033_cesar_chavez`) : justificatif à joindre à un avis, qui obtient alors le badge « verified ».
- `demo_lease_bad_clauses.pdf` : le bail à analyser.
