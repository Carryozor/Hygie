# Jellyfin Support + Settings Tabs Redesign

## Goal

Ajouter la compatibilité Jellyfin par auto-détection, refondre l'interface Paramètres en onglets horizontaux par service, et permettre de configurer les intervalles de scan en heures ou minutes.

## Contrainte critique

**Aucun push git ou Docker avant validation manuelle par l'utilisateur.**

---

## 1. Compatibilité Jellyfin

### Auto-détection

Lors du test de connexion Emby/Jellyfin, Hygie appelle `GET /System/Info` et lit le champ `ProductName` de la réponse :
- `"Emby Server"` → stocke `media_server_type = "emby"`
- `"Jellyfin Server"` → stocke `media_server_type = "jellyfin"`
- Autre / erreur → `media_server_type = ""`

Ce champ est stocké en DB (`settings` table, clé `media_server_type`). Il est **non sensible** (pas dans `SENSITIVE_KEYS`), mis à jour automatiquement à chaque test de connexion réussi.

### Fonctionnalités conditionnelles

Quand `media_server_type == "jellyfin"` :
- Les champs **Collection "Bientôt supprimé"** et **Overlay d'affiches** sont masqués dans l'UI et leurs jobs sont désactivés côté backend
- Un message explicatif s'affiche dans l'onglet Serveur Multimédia

Le reste de la logique (scan, suppression, notifications) est identique — l'API REST est compatible.

### Adaptation backend (`emby_client.py`)

La fonction `test_connection()` est modifiée pour retourner aussi le type détecté :
```python
async def test_connection() -> tuple[bool, str, str]:
    # retourne (ok, message, server_type)  ex: (True, "Emby 4.8.12", "emby")
```

Elle sauvegarde `media_server_type` en DB via `set_setting()`.

### Adaptation scheduler

Dans `sync_emby_collection()` et `_overlay_poster()` : vérifier `await get_setting("media_server_type") == "jellyfin"` en début de fonction → retourner immédiatement si Jellyfin.

---

## 2. Refonte UI Paramètres — onglets horizontaux

### Structure des onglets

| Onglet | Icône | Contenu |
|--------|-------|---------|
| Général | `fa-sliders` | Intervalles, log level, dry run, rétentions |
| Serveur Multimédia | Logo Emby/Jellyfin/générique | URL, clé API, URL externe, collection, overlay (conditionnel) |
| Radarr | `dashboard-icons/radarr.png` | URL, clé API |
| Sonarr | `dashboard-icons/sonarr.png` | URL, clé API |
| Seerr | `dashboard-icons/overseerr.png` | URL, clé API, URL externe |
| qBittorrent | `dashboard-icons/qbittorrent.png` | URL directe, URL proxy QUI, user, password, action, tag |
| Discord | `dashboard-icons/discord.png` | Webhook URL |

### Icône Serveur Multimédia

- **Non testé** : icône générique `fa-photo-film` avec fond dégradé violet/indigo
- **Emby détecté** : logo officiel `dashboard-icons/emby.png` + badge vert "Emby"
- **Jellyfin détecté** : logo officiel `dashboard-icons/jellyfin.png` + badge violet "Jellyfin"

L'icône se met à jour dynamiquement après un test de connexion réussi (lecture de `media_server_type` depuis la réponse `GET /api/settings`).

### Implémentation HTML/JS

L'onglet actif est géré par un état JS `_activeSettingsTab` (string). La fonction `loadSettings()` conserve l'onglet actif lors des rechargements. Le contenu de chaque onglet est dans une `<div>` avec `id="settings-tab-{name}"`, masquée/affichée par JS.

**Pas de chargement lazy** — tous les champs sont rendus une seule fois dans le DOM (comme aujourd'hui), seule la visibilité change. Aucun impact sur les performances.

### Bouton "Tester" et "Enregistrer"

Chaque onglet conserve son propre bouton "Tester" (en bas). Le bouton "Enregistrer" reste global (barre fixe en bas de la page settings), il sauvegarde tous les champs de tous les onglets en une seule requête — comportement inchangé.

---

## 3. Intervalles en heures ou minutes

### UI

Pour chaque intervalle (scan, vérification suppressions), le champ devient :
```
[  6  ] [ heures ▾ ]   →  toutes les 6h
```
Le `<select>` propose `heures` et `minutes`. Un aperçu texte ("toutes les Xh" ou "toutes les X min") s'affiche à droite en temps réel.

### Backend — changement de schéma DB

Deux nouvelles clés remplacent les anciennes :
- `scan_interval_hours` → `scan_interval_minutes` (défaut : `360` = 6h)
- `deletion_check_interval_hours` → `deletion_check_interval_minutes` (défaut : `60` = 1h)

**Migration automatique** dans `init_db()` : si les anciennes clés existent avec une valeur, les convertir en minutes et insérer les nouvelles. Supprimer les anciennes via `ALTER TABLE` n'est pas nécessaire — elles sont simplement ignorées.

APScheduler est configuré en minutes :
```python
scheduler.add_job(run_scan, "interval", minutes=scan_min, id="scan_job", ...)
```

La fonction `reschedule_job` dans `settings.py` est mise à jour en conséquence.

### Frontend — conversion

Quand `loadSettings()` lit `scan_interval_minutes` :
- Si valeur % 60 == 0 → afficher `valeur/60` heures dans le champ, sélectionner "heures"
- Sinon → afficher la valeur brute en minutes, sélectionner "minutes"

Quand `saveSettings()` envoie les données :
- Si "heures" sélectionné → multiplier par 60 avant envoi
- Si "minutes" → envoyer tel quel

---

## 4. Corrections XSS résiduelles (dans le même commit)

Profiter du refactoring de l'UI settings pour corriger les XSS encore présents :
- `loadLogs()` — `l.message`, `l.level`, `l.source` → `escapeHtml()`
- `openIgnoreModal()` — `title`, `libraryName` → `escapeHtml()`
- `renderUnmonitored()` — onclick injection → data-attributes + `escapeHtml()`
- `toast()` — `msg` → DOM API au lieu d'innerHTML
- Terme de recherche vide → `escapeHtml(search)`

---

## 5. Fichiers modifiés

| Fichier | Changement |
|---------|-----------|
| `backend/database.py` | Ajouter `media_server_type`, `scan_interval_minutes`, `deletion_check_interval_minutes` dans DEFAULT_SETTINGS ; migration des anciennes clés |
| `backend/emby_client.py` | `test_connection()` retourne le type détecté ; sauvegarder `media_server_type` |
| `backend/scheduler.py` | `sync_emby_collection()` et overlay : skip si Jellyfin ; utiliser `scan_interval_minutes` |
| `backend/main.py` | Scheduler configuré en minutes |
| `backend/routers/settings.py` | Exposer les nouveaux champs dans `SettingsUpdate` ; reschedule en minutes |
| `frontend/templates/index.html` | Refonte section paramètres : onglets + champs interval avec select |
| `frontend/static/js/app.js` | Logique onglets settings + conversion heures/minutes + correction XSS résiduels |
| `frontend/static/js/i18n.js` | Traductions des nouveaux labels |

---

## 6. Ordre d'implémentation

1. Backend : `database.py` (nouveaux settings, migration)
2. Backend : `emby_client.py` (détection + sauvegarde type)
3. Backend : `scheduler.py` + `main.py` (Jellyfin skip + minutes)
4. Backend : `routers/settings.py` (nouveaux champs)
5. Frontend HTML : refonte section paramètres (onglets)
6. Frontend JS : logique onglets + intervalles + icône dynamique
7. Frontend JS : corrections XSS résiduels
8. Test intégration → validation utilisateur → build + push

---

## Auto-review

- [x] Pas de placeholder ou TBD
- [x] Migration DB sans perte de données (conversion h→min au démarrage)
- [x] Compatibilité ascendante : utilisateurs existants gardent leurs settings convertis
- [x] Aucun push avant validation utilisateur
- [x] Jellyfin skip est défensif (si `media_server_type != "emby"` plutôt que `== "jellyfin"` pour couvrir les cas inconnus)
- [x] Icône générique via Font Awesome (déjà disponible, pas de dépendance supplémentaire)
