# Know Your Building — backend (Personne 1)

## Lancer
    pip install -r requirements.txt
    cp .env.example .env        # mettre MISTRAL_API_KEY
    uvicorn main:app --reload --port 8000

Front : http://localhost:8000/ · Docs API interactives : http://localhost:8000/docs

## État de llm.py (Personne 2)
    curl localhost:8000/api/health

Chaque fonction de llm.py absente ou en erreur bascule sur un repli (rapport par règles,
filtre email/téléphone, matching par budget). Le front fonctionne donc dès maintenant.
Signatures attendues :
- generate_report(building, reviews, lang) -> Report
- moderate_review(text) -> {"ok", "cleaned_text", "reason"}
- verify_proof(file_bytes, mime, expected_address) -> {"verified", "extracted_address", "period", "reason"}
- match_roommates(seeker, candidates) -> [{"profile", "score", "why"}]

## Routes
    GET  /api/report?address=907%20Valencia%20St&lang=en   -> {building, report, community}
    GET  /api/community?blklot=3609042                      -> Community (rafraîchir après un avis)
    POST /api/reviews   multipart : review=<JSON ReviewIn>, proof=<fichier optionnel>
    POST /api/profiles  JSON ProfileIn
    GET  /api/roommates?profile_id=2
    POST /api/contact   JSON {from_profile_id, to_profile_id, message}
    GET  /api/health

## Tests rapides
    curl "localhost:8000/api/report?address=907%20Valencia%20St" | head -c 600
    curl -X POST localhost:8000/api/reviews \
      -F 'review={"blklot":"3609042","pseudo":"test","ratings":{"overall":4},"text":"Bien situé"}'
    curl "localhost:8000/api/roommates?profile_id=2"

## Adresses de démo (trouvées par find_demo_addresses.py, données réelles)
| Rôle          | Adresse              | blklot   | Points clés |
|---------------|----------------------|----------|-------------|
| Problématique | 907 Valencia St      | 3609042  | 17 plaintes DBI ouvertes, 25 dossiers d'infraction, 33 avis d'éviction sur le bloc |
| Bien tenu     | 2288 Broadway        | 0564C104 | 30 logements, 0 plainte en 10 ans |
| Évictions     | 3033 Cesar Chavez St | 5505019  | 9 évictions Ellis Act / owner move-in sur le bloc en 5 ans |

→ Donner ces blklot à la Personne 3 pour seed/seed_community.json (BLKLOT_A/B/C).

## Mocks
- mocks/mock_building.json : BuildingData réel (907 Valencia St)
- mocks/mock_api_report.json : réponse complète de /api/report (pour le front)
- mocks/mock_report.json, mocks/mock_community.json : parties séparées
  (mock_report = rapport de repli ; la Personne 2 le remplace par une vraie sortie Mistral)

## Notes
- Évictions et loyers sont anonymisés au BLOC DE RUE par la ville ("900 Block Of Valencia Street").
- Les justificatifs ne sont jamais écrits sur disque (lus en mémoire, puis jetés).
- Cache mémoire par adresse : préchauffer les 3 adresses de démo avant de passer devant le jury.
- Une adresse absente de la base (ex. logements sociaux) est rattachée au numéro le plus proche
  de la même rue ; matched_address l'indique.
- seed/seed_community.json fourni est minimal : la Personne 3 le remplace (même format).
  Pour recharger : supprimer community.db puis relancer.
