# Contrat d'interface

## GET /api/building?address=...  (BE)
Renvoie des données agrégées. Exemple : `mocks/building.json`.

## POST /api/summary  (LLM)
Body : l'objet renvoyé par /api/building. Réponse : `mocks/summary.json`.
risk_level : low | medium | high. red_flags[].source : evictions | dbi | permits | fire | 311.

## POST /api/lease  (LLM)  multipart/form-data, champ `file` (PDF/image)
Réponse : `mocks/lease.json`. severity : low | medium | high.

## Datasets DataSF (data.sf.gov/resource/<id>.json)
evictions 5cei-gny5 · plaintes DBI gm2e-bten · NOV DBI nbtm-fbw5 · permis i98e-djp9 · rôle fiscal wv5m-vpq2 · 311 vw6y-z8j6 · pompiers wr8u-xric
