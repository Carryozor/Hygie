# Hygie — Manuel d'exploitation

App de nettoyage de médiathèque (Emby/Jellyfin/Plex + *arr). Backend FastAPI (Python 3.12), frontend Vue 3 + Vite + Pinia, DB SQLite **ou** MariaDB via l'abstraction `db/engine.py`. Version dans `backend/version.py` (+ `frontend/vue/package.json`, garder synchro).

**Prod** : conteneur `hygie` de la stack `media-stack` (`/opt/media/media-stack`), image `ghcr.io/carryozor/hygie`, DB = conteneur `mariadb-hygie`. Ce repo (`/opt/claude/hygie`) est le clone de dev — la prod ne se modifie jamais ici directement.

## Commandes

| Action | Commande |
|---|---|
| Dev backend | `make dev` (uvicorn :8000) |
| Tests backend | `make test` (pytest ; CI : `--cov-fail-under=50 --timeout=60`) |
| Tests frontend | `cd frontend/vue && npm run test:unit` |
| Lint | `make lint-all` (ruff + eslint) |
| Parité schémas | `make check-schema` — obligatoire après tout changement DB |
| Build image | `make build` |
| Déploiement | `git tag vX.Y.Z && git push origin main vX.Y.Z` — le workflow `ci.yml` (trigger `tags: v*`) build + push l'image ghcr et crée la release GitHub depuis `CHANGELOG.md` |

## Pièges connus — et la règle qui prévient chacun

1. **Dualité SQLite/MariaDB.** Tout SQL passe par `DbConn` (placeholders `?`, rewrites auto). Jamais de `REPLACE INTO` ni de SQL dialecte-spécifique hors `schema.py`/`schema_mariadb.py`. `key` est un mot réservé MariaDB → backticks. *Règle : tout changement de schéma = les DEUX fichiers DDL + une migration + `make check-schema` + le test de parité doit passer.*
2. **Migrations append-only.** `db/migrations.py` (m001–m015+) : on ajoute à la fin, on ne réordonne jamais, on n'édite jamais une migration déjà livrée. La régression v4.1.1 (colonne `seerr_user_rules.name` absente en MariaDB) venait d'une migration incomplète côté MariaDB. *Règle : chaque migration est écrite et testée pour les deux dialectes.*
3. **Auth : le token d'accès n'est PLUS dans localStorage.** Il est en mémoire (`api/tokenStore.js`) + refresh httpOnly (`hygie_refresh`, path `/api/auth`). Le bug sidebar v4.1.1 venait de composants lisant la clé morte `localStorage['hygie_token']`. *Règle : côté front, l'état d'auth se lit UNIQUEMENT via `useAuthStore().isLoggedIn` / `tokenStore.getToken()`.*
4. **SSRF.** Tout endpoint qui fetch une URL fournie par l'utilisateur (test-arr, proxy image, sync seerr) doit bloquer RFC1918/loopback/169.254.169.254 et re-valider **chaque hop de redirect**. Des correctifs existent — les imiter (`proxy.py`, `routers/settings.py`). *Règle : jamais de `httpx` direct sur une URL utilisateur sans passer par ces validations.*
5. **Multi-worker.** SQLite + `WORKERS>1` = erreur critique au démarrage (voulu). Les locks inter-workers = advisory MariaDB `GET_LOCK`. Les caches en mémoire (settings 30s, etc.) sont par-worker : stalesse max 30s acceptée, ne pas "corriger". *Règle : tout nouvel état partagé passe par la DB, pas par une variable module.*
6. **Pipeline de suppression ordonné** (`deletion_pipeline.py`). Les étapes avant `MediaServerStep` ne doivent rien supprimer (l'item doit encore exister côté serveur). *Règle : nouvelle étape = sous-classer `DeletionStep`, l'insérer au bon endroit de `build_default_pipeline()`, et justifier sa position dans le commit.*
7. **Permissions prod.** `mariadb-hygie` tourne en 999:999 ; le dossier `/opt/media/media-stack/hygie/mariadb` doit rester 999:999. Un chown récursif intempestif a déjà été causé par un autre service (shelfmark) — si les permissions dérivent, chercher un mount trop large ailleurs, pas un bug Hygie.
8. **Réseau prod.** Le port 8000 n'est pas exposé publiquement (NIC privée + security group). Ne pas flag ça comme un problème, et ne rien exposer de plus.

## Barre de qualité (critères vérifiables, pas d'adjectifs)

Avant de considérer un changement terminé :
- [ ] `make test` vert, et **jamais plus d'un run complet par vérification** (la suite peut être lourde ; ne pas la boucler)
- [ ] `make lint-all` sans erreur
- [ ] Changement DB → `make check-schema` vert + migration présente pour les deux dialectes
- [ ] Changement front touchant l'auth → vérifié en réel post-login (sidebar, version, libraries chargées)
- [ ] Endpoint nouveau/modifié qui fetch une URL → tests SSRF présents
- [ ] `CHANGELOG.md` mis à jour ; version bumpée si release
- [ ] Messages d'erreur utilisateur en français ; code et commits en anglais (`type: description`)

## Escalade — quand s'arrêter et demander

- Toute suppression de données de prod, ou opération touchant `/opt/media/media-stack` → demander d'abord, avec le rayon d'action exact
- Ambiguïté sur une règle métier de suppression (grace, conditions) → demander, ne pas deviner : une erreur ici supprime des médias
- Migration destructive (DROP/ALTER perdant des données) → proposer un plan de rollback avant d'écrire le code
- Échec de test préexistant sur main → le signaler, ne pas le « réparer » en le modifiant

## Références

`ARCHITECTURE.md` (décisions), `CHANGELOG.md` (historique des bugs — le lire avant de toucher auth/DB), plans historiques dans `docs/superpowers/plans/`.
