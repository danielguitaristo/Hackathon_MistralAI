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
