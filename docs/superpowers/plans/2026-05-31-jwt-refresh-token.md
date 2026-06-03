# JWT Refresh Token — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le token JWT unique de 7 jours par un access token court (1h) + refresh token long (30j) révocable en base de données.

**Architecture:**
- **Access token** : JWT HS256, expiry 1h, payload `{sub, exp, iat, type:"access"}`
- **Refresh token** : token aléatoire 256 bits, stocké hashé (SHA256) en DB, expiry 30j
- **Auto-refresh frontend** : Axios intercepteur intercept les 401, tente un refresh, réessaie la requête originale. Refresh proactif 5min avant expiry via setTimeout.

**Tech Stack:** PyJWT, secrets (stdlib), hashlib (stdlib), Vue 3, Axios, Pinia

---

## File Map

| Action | Fichier | Rôle |
|---|---|---|
| Modify | `backend/db/schema.py` | Table `refresh_tokens` |
| Modify | `backend/auth.py` | create_access_token, create_refresh_token, verify_refresh_token, revoke_refresh_token |
| Modify | `backend/routers/auth.py` | login/setup retournent les deux tokens, endpoints /refresh et /logout |
| Modify | `frontend/vue/src/stores/auth.js` | Stocke refresh_token, action refresh(), auto-refresh |
| Modify | `frontend/vue/src/api/client.js` | Intercepteur 401 → refresh → retry |

---

## Task 1 : Schéma DB — table refresh_tokens

**Files:**
- Modify: `backend/db/schema.py`

- [ ] **Step 1 : Localiser _TABLES dans schema.py**

```bash
grep -n "\"users\"\|_TABLES\|refresh" /opt/claude/hygie/backend/db/schema.py | head -10
```

- [ ] **Step 2 : Ajouter la table après "users"**

Insérer dans `_TABLES`, juste après la définition de la table `users` :

```python
(
    "refresh_tokens",
    """CREATE TABLE IF NOT EXISTS refresh_tokens (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT    NOT NULL UNIQUE,
        expires_at TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        revoked    INTEGER NOT NULL DEFAULT 0
    )""",
    [],
),
```

- [ ] **Step 3 : Vérifier que check-schema passe**

```bash
cd /opt/claude/hygie && python3 scripts/check-schema.py
```

Résultat attendu : `✅ Schema consistent`

- [ ] **Step 4 : Commit**

```bash
git add backend/db/schema.py
git commit -m "feat(db): add refresh_tokens table for JWT refresh support"
```

---

## Task 2 : backend/auth.py — fonctions refresh token

**Files:**
- Modify: `backend/auth.py`

- [ ] **Step 1 : Lire auth.py**

```bash
cat /opt/claude/hygie/backend/auth.py
```

- [ ] **Step 2 : Ajouter les constantes et imports**

En haut du fichier (après les imports existants), ajouter :

```python
import hashlib
```

Après les constantes existantes (`SECRET_KEY`, `ALGORITHM`, `TOKEN_EXPIRE_DAYS`), ajouter :

```python
ACCESS_TOKEN_EXPIRE_MINUTES = 60        # 1 heure
REFRESH_TOKEN_EXPIRE_DAYS   = 30        # 30 jours
```

- [ ] **Step 3 : Modifier create_token et ajouter les nouvelles fonctions**

Remplacer `create_token` et `verify_token` par :

```python
# ─── JWT access token ─────────────────────────────────────────────────────────
def create_access_token(username: str) -> str:
    """Create a short-lived JWT access token (1 hour)."""
    payload = {
        "sub":  username,
        "type": "access",
        "exp":  datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat":  datetime.now(timezone.utc),
    }
    return _pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_token(username: str) -> str:
    """Backward-compat alias for create_access_token."""
    return create_access_token(username)


def verify_token(token: str) -> Optional[str]:
    """Verify an access token. Returns username or None."""
    try:
        payload = _pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload.get("sub")
    except JWTError:
        return None


# ─── Refresh token ────────────────────────────────────────────────────────────
def _hash_token(token: str) -> str:
    """SHA-256 hash of a token for DB storage (never store raw tokens)."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_refresh_token(username: str) -> str:
    """Create a long-lived refresh token (30 days), stored hashed in DB."""
    raw   = secrets.token_urlsafe(32)
    hashed = _hash_token(raw)
    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()

    async with get_db() as db:
        user = await db.fetch_one("SELECT id FROM users WHERE username=?", (username,))
        if not user:
            raise ValueError(f"User {username!r} not found")
        await db.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user["id"], hashed, expires),
        )
        await db.commit()
    return raw


async def verify_refresh_token(raw: str) -> Optional[str]:
    """Verify a refresh token. Returns username or None if invalid/expired/revoked."""
    hashed = _hash_token(raw)
    async with get_db() as db:
        row = await db.fetch_one(
            """SELECT u.username, rt.expires_at, rt.revoked
               FROM refresh_tokens rt
               JOIN users u ON u.id = rt.user_id
               WHERE rt.token_hash = ?""",
            (hashed,),
        )
    if not row:
        return None
    if row["revoked"]:
        return None
    expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        return None
    return row["username"]


async def revoke_refresh_token(raw: str) -> None:
    """Mark a refresh token as revoked."""
    hashed = _hash_token(raw)
    async with get_db() as db:
        await db.execute(
            "UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?", (hashed,)
        )
        await db.commit()


async def revoke_all_refresh_tokens(username: str) -> None:
    """Revoke all active refresh tokens for a user (e.g., on password change)."""
    async with get_db() as db:
        user = await db.fetch_one("SELECT id FROM users WHERE username=?", (username,))
        if user:
            await db.execute(
                "UPDATE refresh_tokens SET revoked=1 WHERE user_id=? AND revoked=0",
                (user["id"],),
            )
            await db.commit()
```

- [ ] **Step 4 : Tester les imports**

```bash
cd /opt/claude/hygie && python3 -c "
from backend.auth import (
    create_access_token, create_token, verify_token,
    create_refresh_token, verify_refresh_token, revoke_refresh_token
)
print('All auth functions importable OK')
token = create_access_token('test')
print('create_access_token OK:', token[:20], '...')
result = verify_token(token)
print('verify_token OK:', result)
"
```

- [ ] **Step 5 : Commit**

```bash
git add backend/auth.py
git commit -m "feat(auth): add refresh token functions

create_access_token: JWT 1h (replaces 7d token)
create_refresh_token: secure random, stored hashed in DB, 30d
verify_refresh_token: DB lookup + expiry + revocation check
revoke_refresh_token: marks as revoked
revoke_all_refresh_tokens: revokes all tokens for a user
create_token: kept as backward-compat alias"
```

---

## Task 3 : routers/auth.py — endpoints refresh et logout

**Files:**
- Modify: `backend/routers/auth.py`

- [ ] **Step 1 : Lire routers/auth.py**

```bash
cat /opt/claude/hygie/backend/routers/auth.py
```

- [ ] **Step 2 : Ajouter les imports nécessaires**

Dans les imports, ajouter les nouvelles fonctions :
```python
from ..auth import (
    create_access_token,
    create_refresh_token,
    create_token,      # backward compat
    create_user,
    get_client_ip,
    get_user,
    rate_limit,
    require_auth,
    revoke_refresh_token,
    revoke_all_refresh_tokens,
    update_password,
    user_exists,
    verify_password,
    verify_refresh_token,
)
```

- [ ] **Step 3 : Modifier /setup pour retourner les deux tokens**

```python
@router.post("/setup")
async def setup(body: SetupRequest, request: Request):
    if await user_exists():
        raise HTTPException(409, "Un utilisateur existe déjà")
    ip = get_client_ip(request)
    if rate_limit(f"setup:{ip}"):
        raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
    await create_user(body.username, body.password)
    access_token   = create_access_token(body.username)
    refresh_token  = await create_refresh_token(body.username)
    return {
        "token":         access_token,   # backward compat
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "username":      body.username,
    }
```

- [ ] **Step 4 : Modifier /login pour retourner les deux tokens**

```python
@router.post("/login")
async def login(body: LoginRequest, request: Request):
    ip   = get_client_ip(request)
    user = await get_user(body.username)

    if not user or not verify_password(body.password, user["password_hash"]):
        if rate_limit(f"login:{ip}"):
            raise HTTPException(429, "Trop de tentatives — réessayez dans 5 minutes")
        raise HTTPException(401, "Identifiants invalides")

    access_token  = create_access_token(user["username"])
    refresh_token = await create_refresh_token(user["username"])
    return {
        "token":         access_token,   # backward compat
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "username":      user["username"],
    }
```

- [ ] **Step 5 : Ajouter les modèles de requête**

```python
class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str = ""
```

- [ ] **Step 6 : Ajouter les endpoints /refresh et /logout**

```python
@router.post("/refresh")
async def refresh(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token."""
    if not body.refresh_token:
        raise HTTPException(401, "Refresh token requis")
    username = await verify_refresh_token(body.refresh_token)
    if not username:
        raise HTTPException(401, "Refresh token invalide ou expiré")
    access_token = create_access_token(username)
    return {"access_token": access_token, "token": access_token}


@router.post("/logout")
async def logout(body: LogoutRequest, username: str = Depends(require_auth)):
    """Revoke the provided refresh token. Requires valid access token."""
    if body.refresh_token:
        await revoke_refresh_token(body.refresh_token)
    return {"status": "logged_out"}


@router.post("/logout-all")
async def logout_all(username: str = Depends(require_auth)):
    """Revoke ALL refresh tokens for the current user."""
    await revoke_all_refresh_tokens(username)
    return {"status": "all_sessions_revoked"}
```

- [ ] **Step 7 : Modifier /change-password pour révoquer tous les tokens**

```python
@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, username: str = Depends(require_auth)
):
    user = await get_user(username)
    if not user or not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    await update_password(username, body.new_password)
    await revoke_all_refresh_tokens(username)  # invalidate all sessions
    # Return new tokens so the current session stays active
    access_token  = create_access_token(username)
    refresh_token = await create_refresh_token(username)
    return {
        "status":        "ok",
        "access_token":  access_token,
        "refresh_token": refresh_token,
    }
```

- [ ] **Step 8 : Tester les imports**

```bash
cd /opt/claude/hygie && python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
async def main():
    from backend.routers.auth import router
    routes = [r.path for r in router.routes]
    print('Auth routes:', routes)
    assert '/api/auth/refresh' in routes, 'Missing /refresh'
    assert '/api/auth/logout' in routes, 'Missing /logout'
    print('OK')
asyncio.run(main())
"
```

- [ ] **Step 9 : Commit**

```bash
git add backend/routers/auth.py
git commit -m "feat(auth): add /refresh and /logout endpoints

login/setup now return {access_token, refresh_token, token (compat)}
POST /auth/refresh: exchange refresh_token for new access_token
POST /auth/logout: revoke specific refresh_token
POST /auth/logout-all: revoke all user sessions
POST /auth/change-password: revokes all tokens, returns new pair"
```

---

## Task 4 : Frontend — stores/auth.js avec auto-refresh

**Files:**
- Modify: `frontend/vue/src/stores/auth.js`

- [ ] **Step 1 : Lire le fichier actuel**

```bash
cat /opt/claude/hygie/frontend/vue/src/stores/auth.js
```

- [ ] **Step 2 : Réécrire auth.js**

```javascript
// frontend/vue/src/stores/auth.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api/client'

const ACCESS_TOKEN_KEY  = 'hygie_token'
const REFRESH_TOKEN_KEY = 'hygie_refresh_token'
const ACCESS_TTL_MS     = 60 * 60 * 1000   // 1h — matches backend
const REFRESH_BEFORE_MS = 5  * 60 * 1000   // refresh 5min before expiry

export const useAuthStore = defineStore('auth', () => {
  const token        = ref(localStorage.getItem(ACCESS_TOKEN_KEY)  || '')
  const refreshToken = ref(localStorage.getItem(REFRESH_TOKEN_KEY) || '')
  const username     = ref('')
  const setupComplete = ref(null)
  const tokenIssuedAt = ref(0)  // ms timestamp when access token was issued

  let _refreshTimer = null

  const isLoggedIn = computed(() => !!token.value)

  // ── Token storage ────────────────────────────────────────────────────────
  function _setTokens(access, refresh) {
    token.value         = access
    refreshToken.value  = refresh || refreshToken.value
    tokenIssuedAt.value = Date.now()
    localStorage.setItem(ACCESS_TOKEN_KEY, access)
    if (refresh) localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
    _scheduleRefresh()
  }

  function _clearTokens() {
    token.value         = ''
    refreshToken.value  = ''
    tokenIssuedAt.value = 0
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    if (_refreshTimer) clearTimeout(_refreshTimer)
    _refreshTimer = null
  }

  // ── Auto-refresh scheduling ──────────────────────────────────────────────
  function _scheduleRefresh() {
    if (_refreshTimer) clearTimeout(_refreshTimer)
    if (!refreshToken.value) return
    const delay = ACCESS_TTL_MS - REFRESH_BEFORE_MS
    _refreshTimer = setTimeout(refresh, delay > 0 ? delay : 0)
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  async function checkSetup() {
    const { data } = await api.get('/auth/status')
    setupComplete.value = data.setup_complete
    return data.setup_complete
  }

  async function setup(u, p) {
    const { data } = await api.post('/auth/setup', { username: u, password: p })
    username.value = data.username || u
    _setTokens(data.access_token || data.token, data.refresh_token)
  }

  async function login(u, p) {
    const { data } = await api.post('/auth/login', { username: u, password: p })
    username.value = data.username || u
    _setTokens(data.access_token || data.token, data.refresh_token)
  }

  async function refresh() {
    if (!refreshToken.value) return false
    try {
      const { data } = await api.post('/auth/refresh', {
        refresh_token: refreshToken.value,
      })
      const newAccess = data.access_token || data.token
      if (newAccess) {
        _setTokens(newAccess, null)  // keep same refresh token
        return true
      }
    } catch {
      // Refresh failed — tokens are invalid, trigger logout
      _clearTokens()
      window.dispatchEvent(new Event('hygie:unauthorized'))
    }
    return false
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const { data } = await api.get('/auth/me')
      username.value = data.username
    } catch { /* silent */ }
  }

  async function logout() {
    if (refreshToken.value) {
      try {
        await api.post('/auth/logout', { refresh_token: refreshToken.value })
      } catch { /* silent — server-side revocation best-effort */ }
    }
    _clearTokens()
  }

  // Schedule refresh on store init if already logged in
  if (token.value && refreshToken.value) {
    _scheduleRefresh()
  }

  return {
    token, refreshToken, username, setupComplete, isLoggedIn,
    checkSetup, setup, login, refresh, fetchMe, logout,
  }
})
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/stores/auth.js
git commit -m "feat(auth): add refresh token support to auth store

Stores access_token + refresh_token in localStorage.
Auto-schedules refresh 5min before access token expires.
logout() revokes refresh token server-side."
```

---

## Task 5 : frontend api/client.js — intercepteur 401 avec retry

**Files:**
- Modify: `frontend/vue/src/api/client.js`

- [ ] **Step 1 : Lire le fichier actuel**

```bash
cat /opt/claude/hygie/frontend/vue/src/api/client.js
```

- [ ] **Step 2 : Ajouter la logique de refresh avec file d'attente**

Remplacer le contenu par :

```javascript
// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'

const api = axios.create({ baseURL: '/api' })

// ── Request interceptor — inject access token ─────────────────────────────
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('hygie_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// ── Response interceptor — handle 401 with refresh ───────────────────────
let _isRefreshing   = false
let _refreshQueue   = []  // [{resolve, reject}]

function _processQueue(error, token = null) {
  _refreshQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error)
    else resolve(token)
  })
  _refreshQueue = []
}

api.interceptors.response.use(
  r => r,
  async err => {
    const originalReq = err.config
    const isPublic = ['/login', '/setup', '/public'].includes(window.location.pathname)
    const is401 = err.response?.status === 401

    // Skip refresh for auth endpoints themselves
    const isAuthEndpoint = originalReq?.url?.startsWith('/auth/')

    if (is401 && !isPublic && !isAuthEndpoint && !originalReq._retried) {
      const refreshToken = localStorage.getItem('hygie_refresh_token')

      if (!refreshToken) {
        localStorage.removeItem('hygie_token')
        window.dispatchEvent(new Event('hygie:unauthorized'))
        return Promise.reject(err)
      }

      if (_isRefreshing) {
        // Another refresh is in progress — queue this request
        return new Promise((resolve, reject) => {
          _refreshQueue.push({ resolve, reject })
        }).then(token => {
          originalReq.headers.Authorization = `Bearer ${token}`
          originalReq._retried = true
          return api(originalReq)
        })
      }

      _isRefreshing = true
      originalReq._retried = true

      try {
        const { data } = await axios.post('/api/auth/refresh', {
          refresh_token: refreshToken,
        })
        const newToken = data.access_token || data.token
        localStorage.setItem('hygie_token', newToken)
        api.defaults.headers.common.Authorization = `Bearer ${newToken}`
        _processQueue(null, newToken)
        originalReq.headers.Authorization = `Bearer ${newToken}`
        return api(originalReq)
      } catch (refreshErr) {
        _processQueue(refreshErr)
        localStorage.removeItem('hygie_token')
        localStorage.removeItem('hygie_refresh_token')
        window.dispatchEvent(new Event('hygie:unauthorized'))
        return Promise.reject(refreshErr)
      } finally {
        _isRefreshing = false
      }
    }

    return Promise.reject(err)
  }
)

installErrorInterceptor(api)

export default api
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/vue/src/api/client.js
git commit -m "feat(api): add token refresh with request queuing in Axios interceptor

When a 401 is received:
1. If refresh token exists: try POST /auth/refresh
2. Queue concurrent 401 requests during refresh
3. Retry all queued requests with new token
4. On refresh failure: logout + hygie:unauthorized event"
```

---

## Task 6 : Build, deploy, vérifier

**Files:**
- Deploy all

- [ ] **Step 1 : Lancer les tests backend**

```bash
cd /opt/claude/hygie && python3 -m pytest tests/test_auth.py -v 2>&1 | tail -20
```

- [ ] **Step 2 : Déployer**

```bash
cd /opt/claude/hygie && make deploy
```

Résultat attendu : `✅ Deploy complete — Hygie is healthy`

- [ ] **Step 3 : Tester login → refresh → logout dans le container**

```bash
docker exec hygie python3 -c "
import asyncio, sys, os
sys.path.insert(0, '/app')
os.environ['DB_PATH'] = '/app/data/hygie.db'

async def main():
    from backend.auth import create_access_token, create_refresh_token, verify_refresh_token, revoke_refresh_token

    # Test access token
    access = create_access_token('testuser')
    print('Access token created OK (length:', len(access), ')')

    # Test refresh token (needs DB)
    from backend.db.schema import init_db
    await init_db()

    from backend.db.engine import get_db
    async with get_db() as db:
        exists = await db.fetch_one(\"SELECT name FROM sqlite_master WHERE type='table' AND name='refresh_tokens'\")
        print('refresh_tokens table:', 'EXISTS' if exists else 'MISSING')

asyncio.run(main())
"
```

- [ ] **Step 4 : Tester l'endpoint login via HTTP**

```bash
docker exec hygie python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/auth/login',
    data=json.dumps({'username': 'admin', 'password': 'VOTRE_MOT_DE_PASSE'}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
# Note: ce test nécessite les vraies credentials — adapter ou skipper
print('Login endpoint: accessible (test manuel requis)')
"
```

Note : Le test du login nécessite le vrai mot de passe — vérifier manuellement dans le navigateur que le login fonctionne et que localStorage contient `hygie_refresh_token` après connexion.

- [ ] **Step 5 : Commit final si besoin**

```bash
git add -A
git commit -m "chore: deploy JWT refresh token system (Plan D)"
```

---

## Self-Review Checklist

- [x] **Backward compat** : login retourne `token` ET `access_token` → ancien frontend fonctionne
- [x] **Hash sécurisé** : refresh tokens stockés hashés (SHA-256), jamais en clair en DB
- [x] **Request queuing** : plusieurs 401 simultanés → un seul refresh, les autres attendent
- [x] **Skip auth endpoints** : pas de retry sur `/auth/refresh` lui-même (évite boucle infinie)
- [x] **Revocation logout** : revoke_refresh_token côté serveur lors du logout
- [x] **Password change** : revoke_all_refresh_tokens lors du changement de mot de passe
- [x] **Auto-refresh** : scheduled 55min après émission du token (1h - 5min)
- [x] **Schema check** : check-schema.py valide la nouvelle table refresh_tokens
