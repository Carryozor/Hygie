---
name: release
description: Workflow de release Hygie — à utiliser pour TOUT push vers main qui change du code. Un push = une release GitHub + une image Docker poussée, jamais l'un sans l'autre.
---

# Release Hygie

Règle absolue (demandée par l'utilisateur) : **quand on pousse Hygie, on crée la release GitHub ET on build/pousse l'image Docker**. Un push de code sans release+image est un travail inachevé.

## Checklist ordonnée

1. **Version** : bump `backend/version.py` (`VERSION = "X.Y.Z"`) **et** `frontend/vue/package.json` (`"version"`) — les deux, synchrones. Patch = fix, minor = feature, major = breaking/migration lourde.
2. **CHANGELOG.md** : nouvelle section `## [X.Y.Z] — YYYY-MM-DD` décrivant chaque changement (le format existant fait foi).
3. **Qualité** : `make test` vert (un seul run complet), `make lint-all` propre, `make check-schema` si la DB a bougé.
4. **Commit + tag + push** :
   ```bash
   git add -A && git commit -m "fix|feat: vX.Y.Z — résumé"
   git tag vX.Y.Z
   git push && git push --tags
   ```
5. **Release GitHub** :
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes "<section CHANGELOG>"
   ```
6. **Image Docker** (multi-stage, le build arg VERSION alimente `HYGIE_VERSION`) :
   ```bash
   docker build --build-arg VERSION=X.Y.Z -t ghcr.io/carryozor/hygie:vX.Y.Z -t ghcr.io/carryozor/hygie:latest .
   docker push ghcr.io/carryozor/hygie:vX.Y.Z && docker push ghcr.io/carryozor/hygie:latest
   ```
7. **Déploiement prod** (si demandé) : la prod est le conteneur `hygie` de `/opt/media/media-stack` — `cd /opt/media/media-stack && docker compose pull hygie && docker compose up -d hygie`, puis vérifier en réel : `curl -s http://<ip-conteneur>:8000/api/health` et l'UI (login + sidebar chargée).
8. **Vérification post-release** : `gh release view vX.Y.Z` existe, `docker manifest inspect ghcr.io/carryozor/hygie:vX.Y.Z` répond, la version affichée dans l'UI correspond.

## Pièges

- Ne jamais pousser `latest` seul : toujours le tag versionné + latest.
- Le tag git et le tag image doivent être identiques (`vX.Y.Z`).
- Si `docker push` échoue en auth : `gh auth token | docker login ghcr.io -u Carryozor --password-stdin`.
