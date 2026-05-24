# ScanGuru Portal — Troubleshooting & Runbook

**Version:** 1.0
**Last Updated:** May 24, 2026
**Audience:** Support team, on-call engineers, anyone debugging the portal
**Purpose:** When X happens, do Y. Every error pattern, every fix.

This document is the companion to `PORTAL_ARCHITECTURE.md`. Read that first if you need to understand the system; come here when something is broken.

---

## HOW TO USE THIS DOCUMENT

1. **Identify the symptom.** Search this doc for the error message or symptom description.
2. **Follow the steps verbatim.** Each runbook entry is "if you see X, do Y in order."
3. **If unresolved after the runbook:** Escalate per Section 0.
4. **If you discover a new issue:** Add a runbook entry at the bottom of this file. Future-you will thank you.

---

## 0. ESCALATION PATH

Before diving in: who do you call?

| Severity | Trigger | Action |
|---|---|---|
| **P0 — Site down** | `/health` returns 5xx for >5 min | Page on-call immediately (Amador) |
| **P1 — Login broken** | Doctors can't access portal | Page on-call within 15 min |
| **P2 — Feature broken** | Specific feature failing (uploads, PDFs, etc.) | Notify within 1 hour |
| **P3 — Cosmetic / minor** | UI glitch, slow page, single user affected | Ticket, fix in normal cycle |
| **Security incident** | Suspected breach, leaked secret, unauthorized access | **Immediate page + freeze relevant credentials** |

**Primary contact:** Amador (sap-amador on GitHub, [contact info in 1Password vault])
**Backup contact:** Senthil (for AI service issues only)

---

## 1. QUICK HEALTH CHECKS

When in doubt, run these first. They establish what's working and what isn't.

### 1.1 Is the backend alive?

```bash
curl -i https://scanguru-portal-api-production.up.railway.app/health
```

**Expected:**
```
HTTP/2 200
content-type: application/json

{"status":"healthy","service":"scanguru-portal","version":"0.1.0"}
```

**If 200 + JSON →** backend container is running. Database connection NOT verified by this check.

**If 5xx →** Backend is crashing. Go to Section 2.

**If timeout / no response →** Service is down or network issue. Check Railway dashboard for the service status.

### 1.2 Is the frontend reachable?

```bash
curl -I https://sap-amador.github.io/ScanGuru-web/portal-login.html
```

**Expected:** `HTTP/2 200`

**If 404 →** GitHub Pages misconfigured. See Section 7.

### 1.3 Can the backend talk to Postgres?

This requires the Railway CLI linked to the project.

```bash
cd '/path/to/scanguru-portal-api'   # wherever the local repo is
railway run --service scanguru-portal-api python -c "
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
result = db.execute(text('SELECT count(*) FROM users')).scalar()
print(f'DB reachable, users count: {result}')
"
```

**Expected:** `DB reachable, users count: N`

**If "could not translate host name" →** You're trying to run this from your laptop without Railway tunneling. Either use Railway one-off commands in the dashboard, OR use the public proxy URL approach (Section 4.3).

**If "password authentication failed" →** Postgres credentials are out of sync. See Section 4.

### 1.4 Can a real user log in?

```bash
# Get the admin password from the password manager → copy to clipboard
ADMIN_PW=$(pbpaste | tr -d '\n')
curl -X POST https://scanguru-portal-api-production.up.railway.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"admin@scanguru.net\",\"password\":\"$ADMIN_PW\"}"
unset ADMIN_PW
echo
```

**Expected:**
```json
{"access_token":"eyJhbGc...","token_type":"bearer"}
```

**If `Internal Server Error` (500) →** Backend crashing. See Section 2 (get the traceback).

**If `Invalid credentials` (401) →** Password mismatch. See Section 3.

**If `Unauthorized` (401) →** Same as above.

---

## 2. BACKEND 500 ERRORS

The container is running but specific requests crash. You need the traceback.

### 2.1 Get the fresh traceback

```bash
# In one terminal, watch logs:
railway logs --service scanguru-portal-api

# In another, trigger the failing request:
curl -X POST ... (whatever you were doing)
```

If `railway logs` returns nothing or `No linked project found`:

```bash
cd /path/to/scanguru-portal-api
railway link
# Choose: sap-amador's Projects → wonderful-friendship → production → scanguru-portal-api
railway logs --service scanguru-portal-api | tail -100
```

If that still returns nothing, **use Railway dashboard**:
1. Browser → Railway dashboard → wonderful-friendship project
2. Click `scanguru-portal-api` service
3. Click **Deployments** tab → click the latest "Active" deployment
4. Scroll the log stream to the bottom
5. Trigger the failing request
6. Watch a Python traceback appear within 3 seconds
7. Copy from `Traceback (most recent call last):` to the final exception line

### 2.2 Match the traceback to a known issue

| Traceback ends with | Section |
|---|---|
| `psycopg2.OperationalError: connection to server at "localhost"` | 2.3 |
| `psycopg2.OperationalError: password authentication failed for user "postgres"` | 4.2 |
| `psycopg2.OperationalError: connection refused` | 4.1 |
| `cannot import name 'next_visible_id'` | 2.4 |
| `ImportError: cannot import name ...` | 2.5 |
| `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` | 5.1 |
| `firebase_admin.exceptions.InvalidArgumentError` | 5.2 |
| `ModuleNotFoundError: No module named 'psycopg2'` | 2.6 |
| `bcrypt.__about__` warning (NOT an error) | 6.1 |

### 2.3 "localhost:5432 connection refused" in container

**Cause:** Container is reading `DATABASE_URL` from a baked-in `.env` file (local dev value) instead of from Railway env vars.

**Verify diagnosis:**
```bash
railway run --service scanguru-portal-api bash -c "ls -la /app/.env"
# If file exists → that's the problem
```

**Fix:**

1. Check `.dockerignore` exists in the repo root and contains `.env`:
   ```bash
   cd /path/to/scanguru-portal-api
   cat .dockerignore | grep -E "^\.env$"
   ```
   If missing, create it:
   ```bash
   cat >> .dockerignore <<'EOF'
   .env
   .env.local
   .env.production
   venv/
   __pycache__/
   storage/
   .git/
   EOF
   git add .dockerignore
   git commit -m "Exclude .env from Docker context"
   git push
   ```
2. Wait for Railway to rebuild (~2 min).
3. Re-verify `.env` is gone from container:
   ```bash
   railway run --service scanguru-portal-api bash -c "ls -la /app/.env"
   # Should say "No such file or directory"
   ```

### 2.4 "cannot import name 'next_visible_id'"

**Cause:** A patch script wrote the wrong function name into `app/routers/patients.py`.

**Fix:** The function is `next_visible_id` in `app/utils/visible_id.py`. Verify and fix:

```bash
grep -n "def next" app/utils/visible_id.py
# Should show: def next_visible_id(...)

grep -n "import.*visible" app/routers/patients.py
# Should show: from app.utils.visible_id import next_visible_id
```

If the import name is wrong, edit and push.

### 2.5 Generic ImportError after a deploy

**Cause:** A new dependency in `requirements.txt` failed to install, or an internal module path changed.

**Fix:**
1. Check Railway build logs (not runtime logs) — Deployments tab → click the latest deploy → **Build Logs**
2. Look for `pip install` errors near the end of the build
3. If a dependency failed, check if it needs system libs (often the case for `psycopg2`, `cryptography`)
4. Add to `Dockerfile`'s `apt-get install` line, push, rebuild

### 2.6 "ModuleNotFoundError: No module named 'psycopg2'"

**Cause:** `psycopg2-binary` not in `requirements.txt`, or build cache served a stale layer.

**Fix:**
1. Verify in `requirements.txt`:
   ```
   psycopg2-binary==2.9.10
   ```
2. If present, force a clean rebuild on Railway:
   - Railway → Deployments → latest deploy → three dots → **Redeploy**
   - Or push an empty commit: `git commit --allow-empty -m "trigger rebuild" && git push`

---

## 3. AUTH PROBLEMS

### 3.1 Login returns 401 "Invalid credentials"

**Cause:** Email or password doesn't match what's in `users` table.

**Diagnose — does the user exist?**

```bash
railway run --service scanguru-portal-api python -c "
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
row = db.execute(text(\"SELECT id, email, role, is_active, length(password_hash) AS pwlen FROM users WHERE email = :e\"), {'e': 'admin@scanguru.net'}).fetchone()
if row:
    print(f'Found: id={row.id}, role={row.role}, active={row.is_active}, pw_hash_length={row.pwlen}')
else:
    print('User NOT FOUND')
"
```

**Expected:** `Found: id=..., role=admin, active=True, pw_hash_length=60`

**If user not found →** Run seed script. See Section 3.3.

**If user found, password is the issue →** Reset password. See Section 3.2.

### 3.2 Reset admin password

```bash
cd /path/to/scanguru-portal-api

# 1. Generate a new strong password — to clipboard, never to screen
python3 -c "import secrets, string; chars = string.ascii_letters + string.digits + '-_'; print(''.join(secrets.choice(chars) for _ in range(20)))" | pbcopy

# 2. Paste into password manager NOW — update the "admin@scanguru.net password" entry

# 3. Load into shell variable
ADMIN_PW=$(pbpaste | tr -d '\n')
echo "PW length: ${#ADMIN_PW}"   # MUST be 20

# 4. Get the public Postgres URL (laptop can't reach internal)
PUBLIC_URL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | sed 's/^DATABASE_PUBLIC_URL=//')
echo "Public URL host: $(echo $PUBLIC_URL | sed 's|.*@||' | sed 's|/.*||')"

# 5. Update password hash in DB via public proxy
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" \
ADMIN_PW="$ADMIN_PW" \
python3 -c "
import os
from sqlalchemy import create_engine, text
from app.auth import hash_password
new_hash = hash_password(os.environ['ADMIN_PW'])
e = create_engine(os.environ['DATABASE_URL'])
with e.begin() as conn:
    result = conn.execute(
        text('UPDATE users SET password_hash=:h WHERE email=:e'),
        {'h': new_hash, 'e': 'admin@scanguru.net'}
    )
    print(f'Updated {result.rowcount} row(s)')
"

# 6. Cleanup
unset ADMIN_PW
unset PUBLIC_URL

# 7. Verify the new password works
ADMIN_PW=$(pbpaste | tr -d '\n')
curl -X POST https://scanguru-portal-api-production.up.railway.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"admin@scanguru.net\",\"password\":\"$ADMIN_PW\"}"
unset ADMIN_PW
echo
# Expected: {"access_token":"eyJ...","token_type":"bearer"}
```

### 3.3 Re-seed admin user (if it doesn't exist)

```bash
# Generate password as in 3.2 step 1-3
ADMIN_PW=$(pbpaste | tr -d '\n')
echo "PW length: ${#ADMIN_PW}"

PUBLIC_URL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | sed 's/^DATABASE_PUBLIC_URL=//')

DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" \
python scripts/seed.py \
  --org "ScanGuru Demo Clinic" \
  --access-mode shared \
  --email "admin@scanguru.net" \
  --password "$ADMIN_PW" \
  --name "Admin User"

unset ADMIN_PW
unset PUBLIC_URL
```

**Expected output:**
```
Seeded successfully:
  org_id       = ...
  access_mode  = shared
  user_id      = ...
  email        = admin@scanguru.net
  role         = admin
```

**If "User admin@scanguru.net already exists" →** Use password reset (3.2) instead.

### 3.4 Login works in curl but not in browser

**Cause:** Usually CORS or browser cache.

**Fix:**
1. Open the portal page, open DevTools (Cmd-Opt-J)
2. Check Console tab — look for CORS errors specifically
3. Check Network tab — look for the OPTIONS preflight request

**If CORS error:** verify the frontend's origin is in `CORS_ALLOW_ORIGINS`:
```bash
railway variables --service scanguru-portal-api --kv | grep CORS
```

Should include `https://sap-amador.github.io` (and `https://scanguru.net` when custom domain ships).

**If no CORS error but Network shows 401:** browser may have stale autofilled credentials. Hard refresh (Cmd-Shift-R), clear the form, type credentials manually.

### 3.5 JWT signed but instantly invalid

**Cause:** `JWT_SECRET` changed between when the token was issued and when it was verified.

**Symptoms:**
- Login returns valid token
- Next request with token returns 401
- Token decode fails in backend

**Fix:**
- Confirm `JWT_SECRET` is set as a static value in Railway, not regenerated each deploy
- If recently rotated: all existing sessions are invalid. Users need to log in again. This is expected behavior after JWT_SECRET rotation.

---

## 4. DATABASE PROBLEMS

### 4.1 "connection refused" or "host postgres.railway.internal not found"

**Possible causes:**

**A. Backend running, Postgres service down**
- Check Postgres service status in Railway dashboard
- If crashed: Railway → Postgres service → Deployments → Redeploy

**B. You're running the command from your laptop, not from Railway**
- `postgres.railway.internal` only resolves inside Railway's private network
- Your laptop can't reach it directly
- Use the public proxy URL pattern (Section 3.2 step 4) for laptop-originated queries

**C. DATABASE_URL has wrong hostname**
- Should reference `${{Postgres.RAILWAY_PRIVATE_DOMAIN}}`
- If hardcoded to localhost or proxy URL, fix per Section 4.4

### 4.2 "password authentication failed for user postgres"

**Cause:** `DATABASE_URL` on backend has a stale password that doesn't match Postgres's current `POSTGRES_PASSWORD`.

**This is the #1 cause of deployment-day pain.** Hardcoded passwords drift when Postgres rotates credentials.

**Fix — use Railway reference variables (the permanent fix):**

```bash
railway variables --service scanguru-portal-api --set \
  'DATABASE_URL=postgresql+psycopg2://${{Postgres.POSTGRES_USER}}:${{Postgres.POSTGRES_PASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.POSTGRES_DB}}'
```

**Important details:**
- **Single quotes** around the whole value (so the shell doesn't expand `${{...}}`)
- Railway resolves the `${{Service.VAR}}` syntax at deploy time using live Postgres values
- When Postgres rotates passwords, this auto-updates on the next backend deploy

**Verify after the command:**
```bash
railway variables --service scanguru-portal-api --kv | grep '^DATABASE_URL='
# Should show the literal ${{...}} text
```

Wait ~60s for Railway to redeploy, then test:
```bash
curl https://scanguru-portal-api-production.up.railway.app/health
# Then try login
```

### 4.3 Need to run a one-off DB query from laptop

Railway's `postgres.railway.internal` host isn't reachable from your laptop. Use the public proxy URL instead.

```bash
# Get the public URL (only safe — has the same password as internal URL)
PUBLIC_URL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | sed 's/^DATABASE_PUBLIC_URL=//')

# Run alembic or your script via the public URL
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" alembic upgrade head

# Or psql directly
psql "$PUBLIC_URL"

# Or run a Python script
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" python your_script.py

unset PUBLIC_URL
```

**Security note:** the public URL is reachable from anywhere with the password. Don't share the URL or password. Don't leave it in your shell history (`history -c`).

### 4.4 Migrations need to be run

```bash
PUBLIC_URL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | sed 's/^DATABASE_PUBLIC_URL=//')
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" alembic upgrade head
unset PUBLIC_URL
```

**Expected output:**
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade <old> -> <new>, <description>
```

**If "Target database is not up to date" →** Run `alembic upgrade head` first.

**If "type X already exists" →** Postgres enum migration quirk. Edit the migration file and wrap the enum creation in `enum_type.create(op.get_bind(), checkfirst=True)`.

### 4.5 Nuclear option — wipe DB and start over

**Only do this if data is test data and confirmed expendable.**

```bash
# Get public URL
PUBLIC_URL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | sed 's/^DATABASE_PUBLIC_URL=//')

# Drop ALL tables
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" python3 -c "
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'])
with e.begin() as conn:
    conn.execute(text('DROP SCHEMA public CASCADE;'))
    conn.execute(text('CREATE SCHEMA public;'))
    print('Schema reset.')
"

# Re-run migrations
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" alembic upgrade head

# Re-seed admin (see Section 3.3)
```

**Alternative — delete and recreate the entire Postgres service in Railway:**

1. Railway → Postgres service → Settings → Delete Service
2. Project → + New → Database → PostgreSQL
3. Update `DATABASE_URL` reference if needed (usually auto)
4. Run migrations + seed
5. Clean up the orphaned volume that gets left behind (Settings → Volumes)

---

## 5. STORAGE / FIREBASE PROBLEMS

### 5.1 "Fernet key must be 32 url-safe base64-encoded bytes"

**Cause:** `PHI_ENCRYPTION_KEY` is malformed. Should be 44 chars ending with `=`.

**Fix:**
1. Generate a fresh Fernet key:
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | pbcopy
   ```
2. Update the password manager entry
3. Set on Railway:
   ```bash
   railway variables --service scanguru-portal-api --set "PHI_ENCRYPTION_KEY=$(pbpaste)"
   ```

**CRITICAL:** If patients already exist with PHI encrypted under the old key, **rotating the key makes their data unreadable forever**. Only rotate before any real PHI is stored, or implement re-encryption first.

### 5.2 Firebase authentication errors

**Symptoms:**
- `firebase_admin.exceptions.InvalidArgumentError`
- `Failed to initialize Firebase`
- PDFs not uploading after AI inference

**Diagnose — is the credentials env var set?**
```bash
railway variables --service scanguru-portal-api --kv | grep FIREBASE
# Should show:
# FIREBASE_CREDENTIALS_B64=eyJ... (long base64)
# FIREBASE_STORAGE_BUCKET=scanguru-ai-903a4.firebasestorage.app
```

**Common mistakes:**
- Variable name is `FIREBASE_CREDENTIALS_B64`, NOT `FIREBASE_CREDENTIALS_JSON` (the code expects base64-encoded)
- Bucket name uses `.firebasestorage.app` for newer Firebase projects, NOT `.appspot.com`
- Pasting raw JSON instead of base64-encoded JSON

**Fix — re-encode and reset:**
```bash
# Locate the original service account JSON
ls ~/.scanguru-secrets/
# scanguru-ai-903a4-firebase-adminsdk-fbsvc-8154d6b79a.json

# Base64 encode and copy
base64 -i ~/.scanguru-secrets/scanguru-ai-903a4-firebase-adminsdk-fbsvc-8154d6b79a.json | tr -d '\n' | pbcopy

echo "B64 length: $(pbpaste | wc -c | tr -d ' ')"
# Should be ~3000-4000

# Set in Railway
railway variables --service scanguru-portal-api --set "FIREBASE_CREDENTIALS_B64=$(pbpaste)"
```

### 5.3 Service account key lost or compromised

**If the JSON file is lost:** Generate a new one.
1. Firebase Console → ScanGuru-AI project
2. Project Settings (gear icon) → Service accounts tab
3. **Generate new private key** → confirm
4. Download new JSON
5. Move to `~/.scanguru-secrets/`
6. Re-encode and update Railway env var (Section 5.2)
7. Old key is invalidated automatically

**If the JSON file is compromised:** Same as above, but ALSO:
- Investigate what data might have been accessed (Firebase audit logs)
- Notify clinic admins per HIPAA breach notification rules

### 5.4 PDFs not appearing in storage

**Diagnose:**

```bash
# Check if a recent study has a report row
DATABASE_URL="postgresql+psycopg2://${PUBLIC_URL#postgresql://}" python3 -c "
from sqlalchemy import create_engine, text
import os
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    rows = c.execute(text('SELECT s.id, s.status, r.id AS report_id, r.pdf_storage_key FROM studies s LEFT JOIN reports r ON r.study_id = s.id ORDER BY s.created_at DESC LIMIT 5')).fetchall()
    for r in rows:
        print(f'study={r.id} status={r.status} report={r.report_id} pdf={r.pdf_storage_key}')
"
```

If `report_id` is None → AI service didn't return successfully. Check AI service logs.
If `report_id` exists but `pdf_storage_key` is None → Storage adapter failed. Check Railway backend logs for upload error.
If `pdf_storage_key` exists but file doesn't appear in Firebase Console → Bucket permissions issue. Service account needs `Storage Admin` role.

---

## 6. KNOWN COSMETIC WARNINGS (NOT BUGS)

### 6.1 bcrypt `__about__` warning

**Symptom:**
```
WARNING:passlib.handlers.bcrypt:(trapped) error reading bcrypt version
Traceback (most recent call last):
  File ".../passlib/handlers/bcrypt.py", line 620, in _load_backend_mixin
    version = _bcrypt.__about__.__version__
AttributeError: module 'bcrypt' has no attribute '__about__'
```

**Cause:** `passlib 1.7.4` + `bcrypt 4.x` API mismatch. Cosmetic only — passwords still hash and verify correctly.

**Fix (optional):**
```
# In requirements.txt, pin:
bcrypt<4.1
```
Then redeploy.

**Status:** Tolerated until passlib 1.8 ships.

### 6.2 Railway `[err]` prefix on `[INFO]` log lines

**Symptom:** Log lines like:
```
[err] [2026-05-24 09:25:42 +0000] [1] [INFO] Starting gunicorn 23.0.0
```

**Cause:** Gunicorn writes to stderr by default; Railway tags stderr lines as `[err]`. The actual log level is `INFO`, not an error.

**Fix:** None needed. Ignore the `[err]` prefix; look at the bracketed log level (`[INFO]`, `[ERROR]`, etc).

---

## 7. FRONTEND / GITHUB PAGES PROBLEMS

### 7.1 GitHub Pages serves README instead of portal

**Cause:** Repo has no `index.html`, so GitHub Pages renders README.md as the root.

**This is expected behavior.** The portal pages are at specific URLs:
- `https://sap-amador.github.io/ScanGuru-web/portal-login.html`
- `https://sap-amador.github.io/ScanGuru-web/dashboard-main.html`
- `https://sap-amador.github.io/ScanGuru-web/patient-detail.html`
- `https://sap-amador.github.io/ScanGuru-web/patients.html`

**If you want the root URL to be the login page:** Rename `portal-login.html` → `index.html` and update all links.

### 7.2 Login button does nothing in browser

**Diagnose:**
1. Open DevTools (Cmd-Opt-J)
2. Click login
3. Look at Console + Network tabs

**Console shows CORS error →** Frontend origin not in backend's `CORS_ALLOW_ORIGINS`. Update env var (see 3.4).

**Console shows 401 but you know the password is right →**
- Browser autofilled stale credentials
- Hard refresh (Cmd-Shift-R)
- Clear sessionStorage: DevTools → Application → Storage → Clear site data
- Try in Incognito window

**Console shows 500 →** Backend crashing. Go to Section 2.

**Console shows nothing, Network shows pending →** API_BASE URL is wrong (typo, missing https://, etc). Inspect the HTML file's `<script>` block.

### 7.3 API_BASE points at localhost in deployed frontend

**Cause:** Someone pushed HTML files without updating the production URL.

**Verify:**
```bash
cd /path/to/ScanGuru-web
grep "API_BASE" *.html
# All four should show:
# const API_BASE = 'https://scanguru-portal-api-production.up.railway.app/api/v1';
```

**Fix:**
```bash
sed -i.bak "s|http://localhost:8001/api/v1|https://scanguru-portal-api-production.up.railway.app/api/v1|g" \
  portal-login.html dashboard-main.html patient-detail.html patients.html

# Verify
grep -c "scanguru-portal-api-production" *.html | grep -v ":0$"
# Each file should show :1 (one match)

# Cleanup backups
rm *.bak

# Commit
git add -A
git commit -m "Fix API_BASE to production URL"
git push
```

GitHub Pages auto-redeploys within ~30 seconds.

### 7.4 Old version of frontend still cached in browser

**Symptoms:** You pushed an update, but doctors still see the old version.

**Fix:**
- Tell users: Hard refresh (Cmd-Shift-R on Mac, Ctrl-Shift-R on PC)
- Or close all tabs and reopen
- Long-term fix: add cache-busting query strings to HTML resources or set Cache-Control headers (requires moving off GitHub Pages)

---

## 8. RAILWAY DEPLOYMENT ISSUES

### 8.1 Auto-deploy not triggering on push

**Diagnose:**
1. Railway → backend service → Deployments tab
2. Check timestamp on latest deployment
3. If older than your last `git push`: GitHub webhook may have failed

**Fix:**
- Railway → Service Settings → check **Source Repo** is correct
- Settings → Auto-Deploy → confirm it's enabled
- Manually trigger: Deployments tab → top deployment → three dots → **Redeploy**
- Last resort: disconnect and reconnect the GitHub repo

### 8.2 Deployment succeeds but old code is running

**Cause:** Docker layer cache served stale layers.

**Fix:**
```bash
git commit --allow-empty -m "trigger clean rebuild"
git push
```

Or via Railway dashboard: select latest deploy → three dots → **Redeploy** (uses cached layers) or **Build New** (full rebuild).

### 8.3 Build fails on `pip install`

**Diagnose:** Build Logs tab in Railway dashboard.

**Common causes:**
- `psycopg2-binary` requires `libpq-dev` (already in Dockerfile, but verify)
- `cryptography` requires Rust toolchain — should work with pre-built wheels, but if it tries to build from source: pin `cryptography` to a known version with wheels for Python 3.11
- A new package added to `requirements.txt` has an unstated system dep

**Fix:** Add the missing system package to Dockerfile's `apt-get install` line, push, rebuild.

### 8.4 Container starts then crashes immediately

**Diagnose:** Deployments tab → click failing deployment → Logs

**Common causes:**
- Required env var missing (JWT_SECRET, PHI_ENCRYPTION_KEY, etc.)
- Env var malformed (PHI_ENCRYPTION_KEY not a valid Fernet key)
- Postgres unreachable (see Section 4)

The traceback will be early in the log, before "Started server process" line.

### 8.5 Variables UI edit doesn't persist

**Symptom:** You edit a variable in Railway dashboard, save, but the change doesn't actually take effect (env var reads as old value).

**Fix:** Use the CLI instead:
```bash
railway variables --service scanguru-portal-api --set 'KEY=VALUE'
```

CLI is atomic; UI sometimes has race conditions with auto-deploys.

### 8.6 Cleanup orphaned volumes

After deleting a service, Railway sometimes leaves orphaned volumes (visible in project canvas but not attached to any service).

```bash
# List all volumes
railway status --json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for v_edge in data['volumes']['edges']:
    v = v_edge['node']
    inst = v['volumeInstances']['edges'][0]['node']
    print(f\"volume={inst['volume']['name']} id={inst['volume']['id']} serviceId={inst['serviceId']}\")
"
```

Volumes with `serviceId: null` are orphaned. Delete via Railway dashboard:
1. Click the orphaned volume tile
2. Settings → Delete Volume

---

## 9. CORS ISSUES

### 9.1 CORS preflight failing

**Symptom in DevTools Network tab:**
```
OPTIONS /api/v1/whatever → CORS error
```

**Cause:** The frontend origin is not in `CORS_ALLOW_ORIGINS`.

**Diagnose:**
```bash
railway variables --service scanguru-portal-api --kv | grep CORS
```

**Fix:**
```bash
railway variables --service scanguru-portal-api --set \
  'CORS_ALLOW_ORIGINS=https://sap-amador.github.io,https://scanguru.net,https://portal.scanguru.net,http://localhost:3000'
```

**Important:**
- No trailing slash on origins (`https://example.com`, NOT `https://example.com/`)
- Exact protocol+host match (https vs http matters)
- Subdomain matters (`scanguru.net` ≠ `www.scanguru.net`)

### 9.2 CORS works for GET but fails for POST

**Cause:** Backend may not be configured for all HTTP methods.

**Check `app/main.py`:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[...],
    allow_credentials=True,
    allow_methods=["*"],      # <-- must include POST, PUT, DELETE, OPTIONS
    allow_headers=["*"],
)
```

If `allow_methods` is restricted, update to `["*"]` or explicitly include all needed methods.

---

## 10. AI SERVICE ISSUES

The AI inference service is a separate codebase (`sap-amador/MedScan-AI`). This runbook covers symptoms visible from the portal side.

### 10.1 AI service returns 5xx

**Diagnose:**
```bash
curl -i https://adorable-simplicity-production.up.railway.app/healthz
# Or whatever the AI service's health endpoint is
```

**If AI service is down:** Portal uploads will time out or fail with 500. The portal can't fix this; the AI service needs separate attention. **Escalate to Senthil + Amador.**

### 10.2 AI inference timeouts on MRI/PET

**Cause:** Synchronous AI calls. MRI/PET exceed Railway's request timeout (~60s).

**Workaround for pilot:** Restrict to fast modalities (CXR, Dental, MSK, CT Brain).

**Permanent fix:** Implement async job queue. The portal's `app/ai_client.py` has a `poll_job()` helper but isn't wired into the create-study flow. Wiring this is ~8-12 hours of work (Celery + Redis + status polling endpoint + frontend polling).

### 10.3 Inference completes but PDF doesn't appear

**Cause path:**
1. AI service generated PDF and uploaded to Firebase
2. AI service returned the PDF URL to portal backend
3. Portal backend tried to re-upload to portal's own storage but failed

**Diagnose:** Check portal backend logs around the time of the failed upload. Look for Firebase or storage errors.

---

## 11. SECURITY INCIDENTS

### 11.1 Suspected secret leak

**If `JWT_SECRET` may be compromised:**

```bash
# Rotate immediately
python3 -c "import secrets; print(secrets.token_hex(32))" | pbcopy

# Update password manager
# Then update Railway
railway variables --service scanguru-portal-api --set "JWT_SECRET=$(pbpaste)"
```

**Consequence:** All active sessions invalidated. Users must log in again. Acceptable disruption.

**If `PHI_ENCRYPTION_KEY` may be compromised:**

**DO NOT immediately rotate.** Rotating the key without re-encrypting first makes existing PHI unreadable forever.

**Proper response:**
1. Investigate scope — what data could have been accessed?
2. Pause portal access (set Railway service to 0 replicas, or change passwords)
3. Determine if HIPAA breach notification is required (involves a lawyer)
4. Plan re-encryption: generate new key, re-encrypt all rows under new key, then deprecate old key

**If `POSTGRES_PASSWORD` may be compromised:**

1. Railway → Postgres service → Variables → edit `POSTGRES_PASSWORD` → new value
2. Backend's `DATABASE_URL` should auto-update via reference variable (verify it does)
3. Investigate scope of access

### 11.2 Unauthorized access detected

**Immediate actions:**
1. Identify affected user accounts via `audit_log`:
   ```sql
   SELECT * FROM audit_log
   WHERE created_at > NOW() - INTERVAL '1 hour'
   ORDER BY created_at DESC
   LIMIT 100;
   ```
2. Disable affected accounts:
   ```sql
   UPDATE users SET is_active = FALSE WHERE id IN (...);
   ```
3. Force re-authentication: rotate `JWT_SECRET`
4. Preserve evidence: backup audit_log table before any data changes
5. Notify Amador immediately

### 11.3 Suspicious activity in logs

**Look for:**
- High volume of failed logins from single IP → bruteforce attempt
- Successful logins from unusual geographies (use Railway logs IP info)
- High volume of data access in short time → data exfiltration attempt
- Failed authorization (403) spikes → privilege escalation attempt

Implement rate limiting on `/auth/login` ASAP (post-pilot hardening item).

---

## 12. PERFORMANCE ISSUES

### 12.1 Dashboard slow to load

**Diagnose:**
1. DevTools → Network tab → reload
2. Look at timing of `/api/v1/dashboard/stats` and `/api/v1/studies?page=1&page_size=100`

**Common causes:**
- Database missing indexes (rare with this schema)
- N+1 query (check for repeated SELECTs in backend logs)
- Postgres on cold start after idle (Railway hibernates)

**Fix:** Profile with `EXPLAIN ANALYZE` on the slow query. Most often: add an index on `(org_id, created_at)` to whichever table is slow.

### 12.2 Upload modal hangs

**Cause:** Sync AI inference taking too long. See Section 10.2.

### 12.3 PDF download slow

**Cause:** PDF served via signed Firebase URL — speed depends on Firebase's CDN and the doctor's location.

**Fix:** None at portal level. If consistently slow, consider migrating storage to a CDN-fronted bucket (Cloudflare R2 + Cloudflare CDN).

---

## 13. RECOVERY PROCEDURES

### 13.1 Full backend restore from scratch

**If everything is lost (Railway project deleted, etc):**

1. Recreate Railway project, deploy backend service from GitHub
2. Add Postgres service
3. Set all env vars (Section 6.2 of architecture doc)
4. If Postgres backup exists: restore it
5. If no backup: run migrations + reseed admin
6. Update DNS if domains were attached
7. Update frontend `API_BASE` if URL changed

Estimated time: 1-2 hours assuming password manager has all secrets.

### 13.2 Restore Postgres from backup

**Prerequisites:** Backups enabled in Railway Postgres → Settings → Backups (do this NOW if not done).

1. Railway → Postgres service → Backups tab
2. Select backup → Restore
3. Choose target: same service or new
4. Wait for restore to complete (minutes to hours depending on size)
5. Backend should auto-reconnect via reference variable

### 13.3 Roll back a bad deploy

```bash
# Find the last working deploy commit
git log --oneline -20

# Reset main to that commit
git reset --hard <commit-hash>
git push --force

# Railway auto-redeploys the older code
```

**Warning:** `git push --force` is destructive. Make sure no one else has pushed in between.

**Alternative — Railway dashboard:**
1. Deployments tab
2. Find a working deployment
3. Three dots → **Redeploy** (uses that commit's cached image)

---

## 14. NEW INCIDENT TEMPLATE

When you encounter a new issue not in this runbook, add an entry here. Template:

```markdown
### X.Y "Brief symptom description"

**Date encountered:** YYYY-MM-DD
**Reported by:** [name]
**Severity:** P0/P1/P2/P3

**Symptom:**
- What the user saw
- What the logs showed

**Cause:**
- Root cause analysis

**Diagnosis steps:**
```bash
# Commands to confirm the diagnosis
```

**Fix:**
```bash
# Commands to fix
```

**Prevention:**
- How to avoid this in the future
```

---

## 15. APPENDIX — RAILWAY CLI CHEAT SHEET

```bash
# Login
railway login

# Link local folder to project
cd /path/to/scanguru-portal-api
railway link
# Then choose: workspace → project → environment → service

# Confirm link
railway status

# List all services
railway service list

# View env vars
railway variables --service scanguru-portal-api
railway variables --service scanguru-portal-api --kv     # key=value format

# Set env var
railway variables --service scanguru-portal-api --set 'KEY=VALUE'

# Delete env var
railway variables --service scanguru-portal-api --remove KEY

# Run command in service environment (uses Railway env vars, runs on YOUR LAPTOP)
railway run --service scanguru-portal-api <command>

# View logs
railway logs --service scanguru-portal-api
railway logs --service scanguru-portal-api --tail 100

# Get specific variable value
railway variables --service scanguru-portal-api --kv | grep '^MYVAR='
```

---

## 16. APPENDIX — COMMON SHELL TRICKS USED IN THIS RUNBOOK

**Copy clipboard content to a variable without showing it:**
```bash
SECRET=$(pbpaste | tr -d '\n')
echo "Length: ${#SECRET}"   # only shows length, never value
```

**Pipe a generated value to clipboard, never to screen:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))" | pbcopy
```

**Inspect just the host portion of a connection URL:**
```bash
echo "$DATABASE_URL" | sed 's|.*@||' | sed 's|/.*||'
```

**Apply Postgres URL prefix swap:**
```bash
pbpaste | sed 's|^postgresql://|postgresql+psycopg2://|' | pbcopy
```

**Avoid zsh heredoc indentation gotcha — use single-line curl instead:**
```bash
# DON'T (heredoc fails if leading whitespace on closing marker):
curl ... <<JSON
  {"foo": "$VAR"}
  JSON

# DO:
curl ... -d "{\"foo\": \"$VAR\"}"
```

**Clear shell history when secrets may have been typed:**
```bash
history -c
rm ~/.zsh_history    # zsh
rm ~/.bash_history   # bash
```

---

## CHANGELOG

| Date | Change | Author |
|---|---|---|
| 2026-05-24 | Initial runbook from deployment day learnings | Amador (with AI pair) |
