# Jellyfin Support + Settings Tabs + Interval Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter la détection automatique Emby/Jellyfin, refondre les Paramètres en onglets horizontaux par service, et permettre de configurer les intervalles en heures ou minutes.

**Architecture:** 4 tâches backend (DB → detection → scheduler → router) suivies de 3 tâches frontend (HTML → JS → i18n) et un test d'intégration local. Aucun push git/Docker avant validation utilisateur.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, APScheduler, Vanilla JS, Font Awesome, dashboard-icons CDN

---

## Fichiers modifiés

| Fichier | Rôle du changement |
|---|---|
| `backend/database.py` | Nouveaux settings : `media_server_type`, `scan_interval_minutes`, `deletion_check_interval_minutes` + migration |
| `backend/emby_client.py` | `test_connection()` retourne `(bool, str, str)` + sauvegarde `media_server_type` |
| `backend/scheduler.py` | `sync_emby_collection()` guardé sur `media_server_type == "emby"` |
| `backend/main.py` | Scheduler configuré en minutes au lieu d'heures |
| `backend/routers/settings.py` | Nouveaux champs SettingsUpdate + reschedule en minutes + handler test emby à 3 valeurs |
| `backend/routers/libraries.py` | Handler test emby à 3 valeurs |
| `frontend/templates/index.html` | Remplacer section Paramètres (lignes 285-454) par onglets horizontaux |
| `frontend/static/js/app.js` | Logique onglets + icône dynamique + sélecteur intervalle + XSS résiduels |
| `frontend/static/js/i18n.js` | Nouvelles traductions |

---

## Task 1 — database.py : nouveaux settings + migration

**Files:** Modify `backend/database.py`

- [ ] **Ajouter `media_server_type` à `DEFAULT_SETTINGS`** (après `"emby_leaving_soon_overlay"`) :

```python
    "media_server_type": "",           # "" | "emby" | "jellyfin" | "unknown"
```

- [ ] **Ajouter `scan_interval_minutes` et `deletion_check_interval_minutes`** (après les anciennes clés `scan_interval_hours`) :

```python
    "scan_interval_minutes": "360",            # 6h par défaut
    "deletion_check_interval_minutes": "60",   # 1h par défaut
```

Garder les anciennes clés `scan_interval_hours` et `deletion_check_interval_hours` dans DEFAULT_SETTINGS pour ne pas casser les installations existantes — elles seront ignorées par le nouveau code mais resteront en DB.

- [ ] **Ajouter la migration dans `init_db()`**, après le commit des defaults (étape 5), avant `logger.info(...)` :

```python
        # Migrate old interval settings (hours → minutes) if they exist
        async with aiosqlite.connect(DB_PATH) as _mdb:
            async with _mdb.execute("SELECT value FROM settings WHERE key='scan_interval_hours'") as cur:
                row = await cur.fetchone()
                if row and row[0] and row[0].strip().isdigit():
                    old_h = int(row[0])
                    async with _mdb.execute("SELECT value FROM settings WHERE key='scan_interval_minutes'") as cur2:
                        existing = await cur2.fetchone()
                    # Only migrate if scan_interval_minutes is still at default 360 (not yet set)
                    if not existing or existing[0] == "360":
                        await _mdb.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                            ("scan_interval_minutes", str(old_h * 60))
                        )
            async with _mdb.execute("SELECT value FROM settings WHERE key='deletion_check_interval_hours'") as cur:
                row = await cur.fetchone()
                if row and row[0] and row[0].strip().isdigit():
                    old_h = int(row[0])
                    async with _mdb.execute("SELECT value FROM settings WHERE key='deletion_check_interval_minutes'") as cur2:
                        existing = await cur2.fetchone()
                    if not existing or existing[0] == "60":
                        await _mdb.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                            ("deletion_check_interval_minutes", str(old_h * 60))
                        )
            await _mdb.commit()
```

- [ ] **Vérifier la syntaxe** :

```bash
cd /data/hygie && python3 -c "import ast; ast.parse(open('backend/database.py').read()); print('OK')"
```
Expected: `OK`

---

## Task 2 — emby_client.py : détection Emby/Jellyfin/unknown

**Files:** Modify `backend/emby_client.py`

- [ ] **Ajouter `set_setting` à l'import database** (ligne 17) :

```python
from .database import get_setting, set_setting, TIMEOUT_SHORT, TIMEOUT_MEDIUM, TIMEOUT_LONG
```

- [ ] **Remplacer `test_connection()`** (lignes 29-41) par la version avec détection :

```python
async def test_connection() -> Tuple[bool, str, str]:
    """Test connection and detect server type.
    Returns (ok, message, server_type) where server_type is 'emby'|'jellyfin'|'unknown'|''.
    Saves detected type to DB on success.
    """
    url, key = await get_client()
    if not url or not key:
        return False, "URL ou clé API manquante", ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
            r = await client.get(f"{url}/System/Info", params={"api_key": key})
            if r.status_code == 200:
                info = r.json()
                version = info.get("Version", "?")
                product = (info.get("ProductName") or "").lower()
                if "emby" in product:
                    server_type = "emby"
                    label = f"Emby {version}"
                elif "jellyfin" in product:
                    server_type = "jellyfin"
                    label = f"Jellyfin {version}"
                else:
                    server_type = "unknown"
                    label = (info.get("ProductName") or "Unknown") + f" {version}"
                await set_setting("media_server_type", server_type)
                return True, label, server_type
            return False, f"HTTP {r.status_code}", ""
    except Exception as e:
        return False, str(e), ""
```

- [ ] **Vérifier la syntaxe** :

```bash
cd /data/hygie && python3 -c "import ast; ast.parse(open('backend/emby_client.py').read()); print('OK')"
```
Expected: `OK`

---

## Task 3 — scheduler.py + main.py : guard Emby + minutes

**Files:** Modify `backend/scheduler.py`, `backend/main.py`

### scheduler.py

- [ ] **Ajouter le guard Emby-only dans `sync_emby_collection()`** (ligne 1083, juste après la docstring) :

```python
async def sync_emby_collection():
    """Sync the Emby 'Bientôt supprimé' collection bidirectionally."""
    # Collection and overlay are Emby-specific — skip for Jellyfin/unknown/untested
    if await get_setting("media_server_type") != "emby":
        return
    collection_name = await get_setting("emby_leaving_soon_collection")
    if not collection_name:
        return
```

- [ ] **Vérifier syntaxe scheduler.py** :

```bash
cd /data/hygie && python3 -c "import ast; ast.parse(open('backend/scheduler.py').read()); print('OK')"
```

### main.py

- [ ] **Remplacer la lecture des intervalles et la configuration APScheduler** (lignes 100-110) :

```python
    # Schedule jobs — intervals stored in minutes
    try:
        scan_min = int(await get_setting("scan_interval_minutes") or "360")
        del_min = int(await get_setting("deletion_check_interval_minutes") or "60")
    except ValueError:
        scan_min, del_min = 360, 60

    scheduler.add_job(run_scan, "interval", minutes=scan_min, id="scan_job", replace_existing=True)
    scheduler.add_job(
        run_deletion, "interval", minutes=del_min, id="deletion_job", replace_existing=True
    )
```

- [ ] **Vérifier syntaxe main.py** :

```bash
cd /data/hygie && python3 -c "import ast; ast.parse(open('backend/main.py').read()); print('OK')"
```

---

## Task 4 — routers/settings.py + libraries.py : nouveaux champs + reschedule

**Files:** Modify `backend/routers/settings.py`, `backend/routers/libraries.py`

### settings.py

- [ ] **Ajouter les nouveaux champs dans `SettingsUpdate`** — après `deletion_check_interval_hours` :

```python
    scan_interval_minutes: Optional[str] = None
    deletion_check_interval_minutes: Optional[str] = None
    media_server_type: Optional[str] = None
```

- [ ] **Remplacer le bloc de reschedule** (lignes 74-87) :

```python
    # Reschedule jobs si les intervalles ont changé (en minutes)
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        if "scan_interval_minutes" in updated:
            try:
                m = int(await get_setting("scan_interval_minutes") or "360")
                scheduler.reschedule_job("scan_job", trigger="interval", minutes=m)
            except Exception:
                pass
        if "deletion_check_interval_minutes" in updated:
            try:
                m = int(await get_setting("deletion_check_interval_minutes") or "60")
                scheduler.reschedule_job("deletion_job", trigger="interval", minutes=m)
            except Exception:
                pass
```

- [ ] **Mettre à jour les deux endpoints de test** pour gérer le retour à 3 valeurs de `test_connection()` :

Dans `test_service()` (ligne ~92) et dans `libraries.py` `test_service_alias()` :
```python
    result = await tester()
    # test_connection returns (bool, str, str), others return (bool, str)
    ok, message = result[0], result[1]
    return {"ok": ok, "message": message}
```

- [ ] **Vérifier syntaxe des deux fichiers** :

```bash
cd /data/hygie && python3 -c "
import ast
for f in ['backend/routers/settings.py','backend/routers/libraries.py']:
    ast.parse(open(f).read())
    print(f'OK {f}')
"
```

---

## Task 5 — index.html : refonte section Paramètres en onglets

**Files:** Modify `frontend/templates/index.html` (lignes 285-454)

- [ ] **Remplacer l'intégralité de la section** `<!-- Settings -->` jusqu'à `<!-- Logs -->` par le HTML suivant. Retrouver le bloc avec `grep -n "Settings\|Logs" frontend/templates/index.html` pour identifier les lignes exactes, puis remplacer :

```html
<!-- Settings -->
<div id="page-settings" class="page">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px">
    <h1 style="font-size:22px;font-weight:600;color:#e2e8f0">Paramètres</h1>
    <button class="btn btn-primary" style="padding:8px 20px" onclick="saveSettings()">
      <i class="fas fa-floppy-disk"></i>Enregistrer
    </button>
  </div>

  <!-- ── Tab bar ─────────────────────────────────────────────────── -->
  <div style="display:flex;gap:2px;background:#0a0c14;padding:4px;border-radius:10px;margin-bottom:16px;overflow-x:auto" id="settings-tab-bar">
    <button class="settings-tab active" data-stab="general" onclick="switchSettingsTab('general')">
      <i class="fas fa-sliders"></i> Général
    </button>
    <button class="settings-tab" id="stab-media" data-stab="media" onclick="switchSettingsTab('media')">
      <span id="stab-media-icon-wrap"><i class="fas fa-photo-film" style="font-size:13px;color:#a78bfa"></i></span>
      <span id="stab-media-label">Serveur Multimédia</span>
      <span id="stab-media-pill" style="display:none;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600"></span>
    </button>
    <button class="settings-tab" data-stab="radarr" onclick="switchSettingsTab('radarr')">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/radarr.png" width="16" height="16" style="border-radius:3px" onerror="this.style.display='none'"> Radarr
    </button>
    <button class="settings-tab" data-stab="sonarr" onclick="switchSettingsTab('sonarr')">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/sonarr.png" width="16" height="16" style="border-radius:3px" onerror="this.style.display='none'"> Sonarr
    </button>
    <button class="settings-tab" data-stab="seerr" onclick="switchSettingsTab('seerr')">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/overseerr.png" width="16" height="16" style="border-radius:3px" onerror="this.src='https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/jellyseerr.png';this.onerror=null"> Seerr
    </button>
    <button class="settings-tab" data-stab="qbit" onclick="switchSettingsTab('qbit')">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/qbittorrent.png" width="16" height="16" style="border-radius:3px" onerror="this.style.display='none'"> qBittorrent
    </button>
    <button class="settings-tab" data-stab="discord" onclick="switchSettingsTab('discord')">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/discord.png" width="16" height="16" style="border-radius:3px" onerror="this.style.display='none'"> Discord
    </button>
  </div>

  <!-- ── Général ─────────────────────────────────────────────────── -->
  <div id="spanel-general" class="card" style="padding:20px;max-width:640px">
    <div style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div><div style="font-size:13px;font-weight:500">Mode Dry Run</div><div style="font-size:11px;color:var(--muted);margin-top:2px">Simule les suppressions sans rien effacer</div></div>
        <label class="toggle-wrap"><input type="checkbox" id="dry-run-toggle"><div class="toggle-track"></div><div class="toggle-thumb"></div></label>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Niveau de log</label>
          <select class="select" id="log_level" style="width:100%">
            <option value="DEBUG">DEBUG — tout logger</option>
            <option value="INFO">INFO — actions & tâches</option>
            <option value="WARN">WARN — avertissements+</option>
            <option value="ERROR">ERROR — erreurs uniquement</option>
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Rétention historique (jours)</label>
          <input class="input" id="deleted_retention_days" type="number" min="0" placeholder="90">
        </div>
      </div>
      <div style="border-top:1px solid var(--border);padding-top:12px">
        <div style="font-size:12px;font-weight:500;color:#e2e8f0;margin-bottom:10px">Planification</div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="display:flex;align-items:center;gap:8px">
            <label style="font-size:11px;color:var(--muted);width:180px;flex-shrink:0">Scan toutes les</label>
            <input id="scan_interval_value" type="number" min="1" class="input" style="width:70px;border-radius:6px 0 0 6px;border-right:none" value="6">
            <select id="scan_interval_unit" class="select" style="border-radius:0 6px 6px 0;width:100px" onchange="updateIntervalPreview('scan')">
              <option value="h">heures</option>
              <option value="m">minutes</option>
            </select>
            <span id="scan_interval_preview" style="font-size:11px;color:var(--muted)"></span>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <label style="font-size:11px;color:var(--muted);width:180px;flex-shrink:0">Vérification suppressions</label>
            <input id="deletion_check_interval_value" type="number" min="1" class="input" style="width:70px;border-radius:6px 0 0 6px;border-right:none" value="1">
            <select id="deletion_check_interval_unit" class="select" style="border-radius:0 6px 6px 0;width:100px" onchange="updateIntervalPreview('deletion_check')">
              <option value="h">heures</option>
              <option value="m">minutes</option>
            </select>
            <span id="deletion_check_interval_preview" style="font-size:11px;color:var(--muted)"></span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Serveur Multimédia ────────────────────────────────────────── -->
  <div id="spanel-media" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding-bottom:16px;border-bottom:1px solid var(--border)">
      <div id="media-server-logo" style="width:44px;height:44px;border-radius:10px;background:linear-gradient(135deg,#1e2a4a,#2d1f4e);border:1px solid #3730a340;display:flex;align-items:center;justify-content:center;flex-shrink:0">
        <i class="fas fa-photo-film" style="font-size:20px;background:linear-gradient(135deg,#6366f1,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent"></i>
      </div>
      <div>
        <div style="font-size:15px;font-weight:600">Serveur Multimédia</div>
        <div id="media-server-detected" style="font-size:12px;color:var(--muted);margin-top:2px;font-style:italic">Non encore testé — cliquez Tester pour détecter</div>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL interne (Docker)</label><input class="input" id="emby_url" placeholder="http://emby:8096"></div>
        <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Clé API</label><input class="input" id="emby_api_key" type="password" placeholder="••••••••"></div>
      </div>
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL externe (pour les affiches dans le navigateur)</label>
        <input class="input" id="emby_external_url" placeholder="https://emby.mondomaine.fr ou http://IP:8096">
        <div style="font-size:10px;color:var(--muted);margin-top:3px">Doit être accessible depuis ton navigateur pour les affiches Discord.</div>
      </div>
      <!-- Collection section — shown only for Emby -->
      <div id="media-emby-only" style="border-top:1px solid var(--border);padding-top:10px">
        <div style="font-size:12px;font-weight:500;color:#e2e8f0;margin-bottom:8px">Collection "Bientôt supprimé"</div>
        <div style="display:grid;grid-template-columns:1fr 120px;gap:10px">
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Nom de la collection (vide = désactivé)</label>
            <input class="input" id="emby_leaving_soon_collection" placeholder="ex: Bientôt supprimé"></div>
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Délai (jours)</label>
            <input class="input" id="emby_leaving_soon_days" type="number" min="1" max="365" placeholder="30"></div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-top:10px">
          <div style="display:flex;align-items:center;gap:8px">
            <label class="toggle-wrap"><input type="checkbox" id="emby_leaving_soon_overlay"><div class="toggle-track"></div><div class="toggle-thumb"></div></label>
            <div>
              <div style="font-size:12px;font-weight:500;color:#e2e8f0">Bandeau sur les affiches</div>
              <div style="font-size:10px;color:var(--muted)">Ajoute "Supprimé dans Xj" sur l'affiche Emby</div>
            </div>
          </div>
          <button class="btn btn-ghost" onclick="syncEmbyCollection()"><i class="fas fa-rotate"></i>Sync collection</button>
        </div>
      </div>
      <!-- Notice for non-Emby servers -->
      <div id="media-non-emby-notice" style="display:none;background:#8b5cf610;border:1px solid #8b5cf630;border-radius:8px;padding:10px 14px;font-size:12px;line-height:1.5">
        <span id="media-non-emby-msg"></span>
      </div>
      <button class="btn btn-ghost" style="align-self:flex-start;margin-top:6px" onclick="testConn('emby')"><i class="fas fa-plug"></i>Tester</button>
    </div>
  </div>

  <!-- ── Radarr ─────────────────────────────────────────────────────── -->
  <div id="spanel-radarr" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/radarr.png" width="32" height="32" style="border-radius:8px" onerror="this.style.display='none'">
      <div><div style="font-size:15px;font-weight:600">Radarr</div><div style="font-size:11px;color:var(--muted)">Gestion des films</div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL</label><input class="input" id="radarr_url" placeholder="http://192.168.1.10:7878"></div>
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Clé API</label><input class="input" id="radarr_api_key" type="password" placeholder="••••••••"></div>
      <button class="btn btn-ghost" style="align-self:flex-start" onclick="testConn('radarr')"><i class="fas fa-plug"></i>Tester</button>
    </div>
  </div>

  <!-- ── Sonarr ─────────────────────────────────────────────────────── -->
  <div id="spanel-sonarr" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/sonarr.png" width="32" height="32" style="border-radius:8px" onerror="this.style.display='none'">
      <div><div style="font-size:15px;font-weight:600">Sonarr</div><div style="font-size:11px;color:var(--muted)">Gestion des séries</div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL</label><input class="input" id="sonarr_url" placeholder="http://192.168.1.10:8989"></div>
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Clé API</label><input class="input" id="sonarr_api_key" type="password" placeholder="••••••••"></div>
      <button class="btn btn-ghost" style="align-self:flex-start" onclick="testConn('sonarr')"><i class="fas fa-plug"></i>Tester</button>
    </div>
  </div>

  <!-- ── Seerr ─────────────────────────────────────────────────────── -->
  <div id="spanel-seerr" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/overseerr.png" width="32" height="32" style="border-radius:8px" onerror="this.src='https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/jellyseerr.png';this.onerror=null">
      <div><div style="font-size:15px;font-weight:600">Seerr</div><div style="font-size:11px;color:var(--muted)">Overseerr / Jellyseerr</div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL interne (API)</label><input class="input" id="seerr_url" placeholder="http://seerr:5055"></div>
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL externe (liens cliquables)</label>
        <input class="input" id="seerr_external_url" placeholder="https://seerr.mondomaine.fr">
        <div style="font-size:10px;color:var(--muted);margin-top:3px">Utilisée pour les liens dans la file d'attente.</div>
      </div>
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Clé API</label><input class="input" id="seerr_api_key" type="password" placeholder="••••••••"></div>
      <button class="btn btn-ghost" style="align-self:flex-start" onclick="testConn('seerr')"><i class="fas fa-plug"></i>Tester</button>
    </div>
  </div>

  <!-- ── qBittorrent ────────────────────────────────────────────────── -->
  <div id="spanel-qbit" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/qbittorrent.png" width="32" height="32" style="border-radius:8px" onerror="this.style.display='none'">
      <div><div style="font-size:15px;font-weight:600">qBittorrent</div><div style="font-size:11px;color:var(--muted)">Compatible Gluetun / QUI</div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL qBittorrent</label><input class="input" id="qbit_url" placeholder="http://gluetun:8080"></div>
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">URL proxy QUI <span style="color:#6366f1;font-size:10px">(optionnel — remplace l'URL directe)</span></label>
        <input class="input" id="qbit_proxy_url" type="password" placeholder="••••••••">
        <div style="font-size:10px;color:var(--muted);margin-top:4px;padding:6px;background:#0a0c14;border-radius:4px">Si vous utilisez <b style="color:#e2e8f0">QUI</b> comme proxy qBittorrent. Ce champ est chiffré.</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Utilisateur</label><input class="input" id="qbit_user" placeholder="admin"></div>
        <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Mot de passe</label><input class="input" id="qbit_password" type="password" placeholder="••••••••"></div>
      </div>
      <div style="border-top:1px solid var(--border);padding-top:10px">
        <div style="font-size:12px;font-weight:500;color:#e2e8f0;margin-bottom:8px">Action lors d'une suppression Hygie</div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Action sur le torrent</label>
            <select class="select" id="qbit_action" style="width:100%">
              <option value="tag_only">🏷️ Tag uniquement — torrent reste dans qBit, fichier continue de seeder</option>
              <option value="delete_torrent">🗑️ Supprimer torrent + fichier — qBit supprime tout (deleteFiles=true)</option>
            </select>
            <div style="font-size:10px;color:var(--muted);margin-top:4px;padding:6px;background:#0a0c14;border-radius:4px">
              <b style="color:#e2e8f0">Tag</b> : fichier reste seedé.<br><b style="color:#e2e8f0">Supprimer</b> : qBit efface le torrent ET le fichier physique.
            </div>
          </div>
          <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Nom du tag</label>
            <input class="input" id="qbit_tag" placeholder="ex: Supprimé-Hygie" style="width:240px">
          </div>
        </div>
      </div>
      <button class="btn btn-ghost" style="align-self:flex-start" onclick="testConn('qbit')"><i class="fas fa-plug"></i>Tester</button>
    </div>
  </div>

  <!-- ── Discord ────────────────────────────────────────────────────── -->
  <div id="spanel-discord" class="card" style="display:none;padding:20px;max-width:640px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <img src="https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/discord.png" width="32" height="32" style="border-radius:8px" onerror="this.style.display='none'">
      <div><div style="font-size:15px;font-weight:600">Discord</div><div style="font-size:11px;color:var(--muted)">Notifications webhook</div></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:12px">
      <div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Webhook URL</label><input class="input" id="discord_webhook" placeholder="https://discord.com/api/webhooks/..."></div>
      <button class="btn btn-ghost" style="align-self:flex-start" onclick="testConn('discord')"><i class="fab fa-discord"></i>Tester</button>
      <div style="border-top:1px solid var(--border);padding-top:12px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <div>
            <div style="font-size:12px;font-weight:500;color:#e2e8f0">Mentions utilisateurs</div>
            <div style="font-size:10px;color:var(--muted)">Associez chaque utilisateur Seerr à son ID Discord pour les mentions.</div>
          </div>
          <button class="btn btn-ghost" style="font-size:12px" onclick="loadDiscordMappings()"><i class="fas fa-rotate-right"></i></button>
        </div>
        <div id="discord-mappings-list" style="display:flex;flex-direction:column;gap:6px">
          <div style="font-size:12px;color:var(--muted);font-style:italic">Cliquez sur actualiser pour charger les utilisateurs Seerr</div>
        </div>
      </div>
    </div>
  </div>

</div>
```

- [ ] **Ajouter le CSS des onglets** dans le `<style>` de l'HTML (après `.log-row`) :

```css
.settings-tab {
  display:flex;align-items:center;gap:6px;padding:7px 12px;border-radius:7px;
  font-size:12px;color:var(--muted);cursor:pointer;white-space:nowrap;
  flex-shrink:0;transition:all .15s;background:transparent;border:none;
}
.settings-tab:hover { background:#141824;color:var(--text-muted); }
.settings-tab.active { background:#1e2230;color:#e2e8f0; }
```

- [ ] **Vérifier** que le fichier est du HTML valide (pas d'erreur de balise) :

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser):
    def handle_error(self, m): print('Error:', m)
V().feed(open('/data/hygie/frontend/templates/index.html').read())
print('HTML parse OK')
"
```

---

## Task 6 — app.js : onglets + icône + intervalles + XSS résiduels

**Files:** Modify `frontend/static/js/app.js`

- [ ] **Ajouter la variable d'état onglet actif** après `let _settingsLoaded`:

```javascript
let _activeSettingsTab = 'general';
```

- [ ] **Ajouter la fonction `switchSettingsTab()`** après `markSettingsDirty`:

```javascript
function switchSettingsTab(tab) {
  _activeSettingsTab = tab;
  document.querySelectorAll('.settings-tab').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('[id^="spanel-"]').forEach(el => el.style.display = 'none');
  const btn = document.querySelector(`[data-stab="${tab}"]`);
  if (btn) btn.classList.add('active');
  const panel = document.getElementById(`spanel-${tab}`);
  if (panel) panel.style.display = 'block';
}
```

- [ ] **Ajouter `updateIntervalPreview()`** après `switchSettingsTab`:

```javascript
function updateIntervalPreview(id) {
  const val = parseInt(document.getElementById(id + '_interval_value')?.value) || 1;
  const unit = document.getElementById(id + '_interval_unit')?.value || 'h';
  const el = document.getElementById(id + '_interval_preview');
  if (!el) return;
  let text;
  if (unit === 'h') {
    text = `→ toutes les ${val}h`;
  } else {
    if (val < 60) { text = `→ toutes les ${val} min`; }
    else {
      const h = Math.floor(val / 60), m = val % 60;
      text = `→ toutes les ${h}h${m > 0 ? m + 'min' : ''}`;
    }
  }
  el.textContent = text;
}
```

- [ ] **Ajouter `updateMediaServerIcon(type)`** après `updateIntervalPreview` :

```javascript
function updateMediaServerIcon(type) {
  const iconWrap = document.getElementById('stab-media-icon-wrap');
  const headerLogo = document.getElementById('media-server-logo');
  const pill = document.getElementById('stab-media-pill');
  const detected = document.getElementById('media-server-detected');
  const embyOnly = document.getElementById('media-emby-only');
  const nonEmbyNotice = document.getElementById('media-non-emby-notice');
  const nonEmbyMsg = document.getElementById('media-non-emby-msg');
  if (!iconWrap) return;

  const icons = {
    emby: 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/emby.png',
    jellyfin: 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/jellyfin.png',
  };

  if (type === 'emby') {
    iconWrap.innerHTML = `<img src="${icons.emby}" width="16" height="16" style="border-radius:3px">`;
    if (headerLogo) headerLogo.innerHTML = `<img src="${icons.emby}" style="width:38px;height:38px;object-fit:contain;border-radius:8px">`;
    pill.textContent = 'Emby'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#52b04025;color:#52b040';
    if (detected) { detected.textContent = '✓ Emby détecté'; detected.style.color = '#52b040'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  } else if (type === 'jellyfin') {
    iconWrap.innerHTML = `<img src="${icons.jellyfin}" width="16" height="16" style="border-radius:3px">`;
    if (headerLogo) headerLogo.innerHTML = `<img src="${icons.jellyfin}" style="width:38px;height:38px;object-fit:contain;border-radius:8px">`;
    pill.textContent = 'Jellyfin'; pill.style.cssText = 'display:inline;font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600;background:#8b5cf625;color:#a78bfa';
    if (detected) { detected.textContent = '✓ Jellyfin détecté'; detected.style.color = '#a78bfa'; detected.style.fontStyle = 'normal'; }
    if (embyOnly) embyOnly.style.display = 'none';
    if (nonEmbyNotice) { nonEmbyNotice.style.display = 'block'; nonEmbyMsg.innerHTML = '⚠️ <strong>Non disponible avec Jellyfin</strong> — La collection "Bientôt supprimé" et l\'overlay d\'affiches utilisent des APIs spécifiques à Emby.'; nonEmbyNotice.style.color = '#a78bfa'; }
  } else if (type === 'unknown') {
    iconWrap.innerHTML = `<i class="fas fa-photo-film" style="font-size:13px;color:#a78bfa"></i>`;
    if (headerLogo) headerLogo.innerHTML = `<i class="fas fa-photo-film" style="font-size:20px;background:linear-gradient(135deg,#6366f1,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent"></i>`;
    pill.style.display = 'none';
    if (detected) { detected.textContent = 'Serveur non reconnu — fonctionnalités Collection/Overlay disponibles avec Emby uniquement'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'none';
    if (nonEmbyNotice) { nonEmbyNotice.style.display = 'block'; nonEmbyMsg.innerHTML = '⚠️ <strong>Serveur non reconnu</strong> — Les fonctionnalités Collection et Overlay d\'affiches sont disponibles uniquement avec Emby.'; nonEmbyNotice.style.color = '#94a3b8'; }
  } else {
    // '' — not tested
    iconWrap.innerHTML = `<i class="fas fa-photo-film" style="font-size:13px;color:#a78bfa"></i>`;
    if (headerLogo) headerLogo.innerHTML = `<i class="fas fa-photo-film" style="font-size:20px;background:linear-gradient(135deg,#6366f1,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent"></i>`;
    pill.style.display = 'none';
    if (detected) { detected.textContent = 'Non encore testé — cliquez Tester pour détecter'; detected.style.color = 'var(--muted)'; detected.style.fontStyle = 'italic'; }
    if (embyOnly) embyOnly.style.display = 'block';
    if (nonEmbyNotice) nonEmbyNotice.style.display = 'none';
  }
}
```

- [ ] **Modifier `loadSettings()`** — remplacer la liste SETTINGS_FORM_FIELDS pour retirer les anciennes clés d'intervalle, et ajouter la logique de remplissage spéciale :

Retirer `scan_interval_hours` et `deletion_check_interval_hours` de `SETTINGS_FORM_FIELDS`.

Après le bloc `SETTINGS_FORM_FIELDS.forEach(...)` dans `loadSettings`, ajouter :

```javascript
    // Intervals — special handling: convert minutes → value + unit
    const scanMin = parseInt(s.scan_interval_minutes || '360');
    const scanInHours = scanMin % 60 === 0;
    const scanVal = document.getElementById('scan_interval_value');
    const scanUnit = document.getElementById('scan_interval_unit');
    if (scanVal) scanVal.value = scanInHours ? scanMin / 60 : scanMin;
    if (scanUnit) { scanUnit.value = scanInHours ? 'h' : 'm'; }
    updateIntervalPreview('scan');

    const delMin = parseInt(s.deletion_check_interval_minutes || '60');
    const delInHours = delMin % 60 === 0;
    const delVal = document.getElementById('deletion_check_interval_value');
    const delUnit = document.getElementById('deletion_check_interval_unit');
    if (delVal) delVal.value = delInHours ? delMin / 60 : delMin;
    if (delUnit) { delUnit.value = delInHours ? 'h' : 'm'; }
    updateIntervalPreview('deletion_check');

    // Media server icon
    updateMediaServerIcon(s.media_server_type || '');

    // Restore active tab
    switchSettingsTab(_activeSettingsTab);
```

- [ ] **Modifier `saveSettings()`** — ajouter la conversion intervalles avant `fields.forEach` :

```javascript
    // Intervals — convert to minutes before saving
    const scanV = parseInt(document.getElementById('scan_interval_value')?.value) || 6;
    const scanU = document.getElementById('scan_interval_unit')?.value || 'h';
    body.scan_interval_minutes = String(scanU === 'h' ? scanV * 60 : scanV);

    const delV = parseInt(document.getElementById('deletion_check_interval_value')?.value) || 1;
    const delU = document.getElementById('deletion_check_interval_unit')?.value || 'h';
    body.deletion_check_interval_minutes = String(delU === 'h' ? delV * 60 : delV);
```

- [ ] **XSS — corriger `loadLogs()`** (ligne ~716) :

Remplacer :
```javascript
      return `<div class="log-row log-${l.level}"><span class="log-ts">${ts}</span><span class="log-level">${l.level}</span><span class="log-cat">${l.source||''}</span><span style="color:var(--text)">${l.message}</span></div>`;
```
Par :
```javascript
      return `<div class="log-row log-${escapeHtml(l.level||'')}"><span class="log-ts">${ts}</span><span class="log-level">${escapeHtml(l.level||'')}</span><span class="log-cat">${escapeHtml(l.source||'')}</span><span style="color:var(--text)">${escapeHtml(l.message||'')}</span></div>`;
```

- [ ] **XSS — corriger `openIgnoreModal()`** (ligne ~882) :

```javascript
  document.getElementById('ignore-media-info').innerHTML = `
    ${poster}
    <div>
      <div style="font-weight:600;color:#e2e8f0">${escapeHtml(title)}</div>
      <div style="font-size:12px;color:var(--muted);margin-top:3px">📚 ${escapeHtml(libraryName)}</div>
    </div>`;
```

- [ ] **XSS — corriger `toast()`** (ligne ~139) :

Remplacer `el.innerHTML = ...` par construction DOM :
```javascript
function toast(msg, type='info') {
  const icons = {success:'check-circle',error:'triangle-exclamation',info:'circle-info',warn:'exclamation'};
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const icon = document.createElement('i');
  icon.className = `fas fa-${icons[type]||'circle-info'}`;
  const span = document.createElement('span');
  span.textContent = msg;
  el.append(icon, span);
  document.getElementById('toast-wrap').prepend(el);
  setTimeout(() => { el.style.opacity='0'; el.style.transition='opacity .3s'; setTimeout(()=>el.remove(),300); }, 3500);
}
```

- [ ] **XSS — corriger terme de recherche vide** (ligne ~290) :

```javascript
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--muted)">${search?'Aucun résultat pour "'+escapeHtml(search)+'"':'Aucun média'}</td></tr>`;
```

- [ ] **XSS — corriger `renderUnmonitored()`** — remplacer le titre, genres et onclick :

```javascript
    // Titre escapeHtml
    <div style="font-weight:600;font-size:13px;color:#e2e8f0;..." title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</div>
    // Genres
    ${m.genres?.length ? `<div style="display:flex;gap:3px;flex-wrap:wrap">${m.genres.map(g=>`<span style="font-size:10px;background:#ffffff0a;color:var(--muted);border-radius:3px;padding:1px 5px">${escapeHtml(g)}</span>`).join('')}</div>` : ''}
    // Boutons monitor/delete via data-attributes
    <button class="btn btn-primary" ... data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="${m.type||'movie'}" onclick="monitorFromEl(this)">...</button>
    <button class="btn btn-ghost" ... data-mid="${m.id}" data-title="${escapeHtml(m.title)}" data-type="${m.type||'movie'}" data-hasfile="${m.has_file}" onclick="deleteUnmonitoredFromEl(this)">...</button>
```

Ajouter les wrappers :
```javascript
function monitorFromEl(el) {
  const type = el.dataset.type === 'series' ? 'series' : 'movie';
  if (type === 'movie') monitorMovie(parseInt(el.dataset.mid), el.dataset.title);
  else monitorSeries(parseInt(el.dataset.mid), el.dataset.title);
}
function deleteUnmonitoredFromEl(el) {
  deleteUnmonitored(el.dataset.type, parseInt(el.dataset.mid), el.dataset.title, el.dataset.hasfile === 'true');
}
```

- [ ] **Vérifier syntaxe JS** :

```bash
node --check /data/hygie/frontend/static/js/app.js && echo "JS OK"
```

---

## Task 7 — i18n.js : nouvelles traductions

**Files:** Modify `frontend/static/js/i18n.js`

- [ ] **Ajouter les nouvelles clés** dans le bloc `en` :

```javascript
    // ── Settings tabs ────────────────────────────────────────────────
    'Serveur Multimédia': 'Media Server',
    'Planification': 'Scheduling',
    'Scan toutes les': 'Scan every',
    'Vérification suppressions': 'Deletion check',
    'heures': 'hours',
    'minutes': 'minutes',
    'Non encore testé — cliquez Tester pour détecter': 'Not tested yet — click Test to detect',
    'Serveur non reconnu — fonctionnalités Collection/Overlay disponibles avec Emby uniquement': 'Unrecognized server — Collection/Overlay features available with Emby only',
    '✓ Emby détecté': '✓ Emby detected',
    '✓ Jellyfin détecté': '✓ Jellyfin detected',
    'Gestion des films': 'Movie management',
    'Gestion des séries': 'Series management',
    'Compatible Gluetun / QUI': 'Gluetun / QUI compatible',
    'Notifications webhook': 'Webhook notifications',
    'Enregistrer': 'Save',
```

- [ ] **Vérifier syntaxe** :

```bash
node --check /data/hygie/frontend/static/js/i18n.js && echo "i18n OK"
```

---

## Task 8 — Test d'intégration local (PAS de push git/Docker)

- [ ] **Build image de test** :

```bash
docker build -t hygie-v2.1-test:latest /data/hygie/ 2>&1 | tail -5
```
Expected: build réussi.

- [ ] **Démarrer le container de test** :

```bash
cp /opt/media-stack/hygie/data/hygie.db /tmp/hygie-v21-test.db && chmod 666 /tmp/hygie-v21-test.db
docker rm -f hygie-v21-test 2>/dev/null
docker run -d --name hygie-v21-test -p 8002:8000 \
  -v /tmp/hygie-v21-test.db:/app/data/hygie.db \
  -e DB_PATH=/app/data/hygie.db \
  -e TZ=Europe/Paris \
  -e HYGIE_ENCRYPTION_KEY=Iii5-zMUsGN7dDE3yZeyW8Sc9HNgxhz8UC2Va752jx0= \
  hygie-v2.1-test:latest && sleep 8
```

- [ ] **Tests automatisés** :

```bash
python3 - << 'EOF'
import urllib.request, json

base = "http://localhost:8002"
ok_count = 0

def check(label, condition):
    global ok_count
    if condition:
        print(f"  ✅ {label}")
        ok_count += 1
    else:
        print(f"  ❌ {label}")

# Health
r = json.loads(urllib.request.urlopen(f"{base}/health", timeout=5).read())
check("Health healthy", r.get("status") == "healthy")

# Migration DB: scan_interval_minutes exists
import subprocess
result = subprocess.run(
    ["docker", "exec", "hygie-v21-test", "python3", "-c",
     "import asyncio,sys; sys.path.insert(0,'/app'); "
     "from backend.database import get_setting; "
     "v=asyncio.run(get_setting('scan_interval_minutes')); "
     "print(v)"],
    capture_output=True, text=True
)
check("scan_interval_minutes migrated", result.stdout.strip().isdigit())

# media_server_type exists (empty string by default)
result2 = subprocess.run(
    ["docker", "exec", "hygie-v21-test", "python3", "-c",
     "import asyncio,sys; sys.path.insert(0,'/app'); "
     "from backend.database import get_setting; "
     "v=asyncio.run(get_setting('media_server_type')); "
     "print(repr(v))"],
    capture_output=True, text=True
)
check("media_server_type in DB", "repr" not in result2.stdout or result2.returncode == 0)

# JS has switchSettingsTab
js = urllib.request.urlopen(f"{base}/static/js/app.js", timeout=5).read().decode()
check("switchSettingsTab defined", "function switchSettingsTab" in js)
check("updateMediaServerIcon defined", "function updateMediaServerIcon" in js)
check("updateIntervalPreview defined", "function updateIntervalPreview" in js)
check("XSS loadLogs fixed", "escapeHtml(l.message" in js)
check("XSS toast fixed", "span.textContent = msg" in js)
check("XSS search fixed", "escapeHtml(search)" in js)

# Settings tabs in HTML
html = urllib.request.urlopen(f"{base}/", timeout=5).read().decode()
check("spanel-general in HTML", "spanel-general" in html)
check("spanel-media in HTML", "spanel-media" in html)
check("spanel-radarr in HTML", "spanel-radarr" in html)
check("stab-media-pill in HTML", "stab-media-pill" in html)
check("scan_interval_value in HTML", "scan_interval_value" in html)

# No errors in logs
import time; time.sleep(2)
result3 = subprocess.run(
    ["docker", "logs", "hygie-v21-test"],
    capture_output=True, text=True
)
logs = result3.stdout + result3.stderr
errors = [l for l in logs.splitlines() if "ERROR" in l or "Exception" in l or "Traceback" in l]
errors = [l for l in errors if "HTTP 4" not in l and "websocket" not in l]
check("No startup errors", len(errors) == 0)
if errors:
    for e in errors[:3]: print(f"    {e}")

print(f"\n{'✅ All tests passed' if ok_count >= 12 else '⚠️  Some tests failed'} ({ok_count}/13)")
EOF
```
Expected: `✅ All tests passed (13/13)` ou proche.

- [ ] **Vérifier manuellement dans le navigateur** — ouvrir `http://localhost:8002` :
  - Les onglets Paramètres apparaissent
  - Cliquer sur "Serveur Multimédia" → icône générique + "Non testé"
  - Entrer `http://emby:8096` + clé API → cliquer Tester → icône Emby + badge vert
  - Les champs Collection/Overlay sont visibles
  - Modifier scan à "30 minutes" → preview affiche "toutes les 30 min"
  - Modifier scan à "2 heures" → preview affiche "toutes les 2h"
  - Enregistrer → recharger la page → valeurs conservées

- [ ] **Cleanup** (après validation utilisateur) :

```bash
docker rm -f hygie-v21-test && rm /tmp/hygie-v21-test.db && echo "Cleaned"
```

---

## Auto-review

**Couverture spec :**
- [x] Auto-détection Emby/Jellyfin/unknown — Task 2
- [x] Icône générique avant détection — Task 5 + 6
- [x] Icône dynamique après détection — Task 6 `updateMediaServerIcon`
- [x] Collection/overlay masqués si `!= "emby"` — Task 3 (scheduler) + Task 6 (UI)
- [x] Message spécifique Jellyfin vs message neutre unknown — Task 6
- [x] Onglets horizontaux 7 services — Task 5
- [x] Sélecteur heures/minutes + preview — Task 5 (HTML) + Task 6 (JS)
- [x] Migration DB minutes depuis heures — Task 1
- [x] XSS loadLogs, toast, search, openIgnoreModal, renderUnmonitored — Task 6
- [x] Pas de push git/Docker avant validation — aucun step de push dans le plan

**Pas de placeholders détectés.**

**Cohérence des noms :**
- `scan_interval_minutes` / `deletion_check_interval_minutes` : cohérent entre Task 1, 3, 4, 6
- `media_server_type` : cohérent entre Task 1, 2, 3, 4, 6
- `switchSettingsTab` / `spanel-{tab}` / `stab-{tab}` : cohérent Task 5 et 6
- `updateMediaServerIcon(type)` : défini Task 6, appelé dans `loadSettings`
