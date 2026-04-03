# FieldOps v4 — Deployment Guide

## Option A: Railway (Recommended — Free tier, easy)

Railway auto-detects Python apps and runs them with minimal config.

### Steps

1. **Push code to GitHub**
   ```bash
   git init
   git add .
   git commit -m "FieldOps v4 initial"
   # Create a repo on github.com, then:
   git remote add origin https://github.com/YOUR-USERNAME/fieldops.git
   git push -u origin main
   ```

2. **Connect to Railway**
   - Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
   - Select your repository
   - Railway detects `Procfile` automatically

3. **Set environment variables** in Railway dashboard → Variables:
   ```
   DATABASE_URL   = postgresql://postgres:[PASS]@db.[REF].supabase.co:5432/postgres?sslmode=require
   SECRET_KEY     = (your generated 32+ char key)
   FLASK_DEBUG    = 0
   ```

4. **Deploy** — Railway builds and deploys automatically.

5. **Seed demo data** (first deploy only):
   ```
   https://your-app.railway.app/api/seed
   ```
   Use POST — easiest via curl:
   ```bash
   curl -X POST https://your-app.railway.app/api/seed
   ```

---

## Option B: Render (Also free tier)

1. Go to [render.com](https://render.com) → New Web Service → Connect GitHub repo
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn wsgi:application --workers 2 --bind 0.0.0.0:$PORT`
4. Add environment variables (same as Railway above)
5. Deploy

---

## Option C: Run Locally with Supabase

```bash
# 1. Create .env from example
cp .env.example .env
# Edit .env with your Supabase DATABASE_URL and SECRET_KEY

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python app.py
```

Visit http://localhost:5000 — tables are created automatically on first run.

---

## Supabase Setup (Database)

1. Go to [supabase.com](https://supabase.com) → New Project
2. Choose a strong database password — **save it**
3. Wait for project to provision (~2 minutes)
4. Go to: **Project Settings → Database → Connection string → URI**
5. Copy the URI and add `?sslmode=require` at the end
6. Paste into your `DATABASE_URL` environment variable

That's it — FieldOps creates all tables automatically on startup.

---

## First Login

After deploying:

1. Visit your app URL
2. Click **Register** — the first registered user becomes **Admin**
3. Log in as Admin
4. Go to **Orgs** → create your organization(s)
5. Go to **Admin** → create supervisors and field users
6. Go to **Zones** → create zones for each org
7. Go to **Forms** → build your data collection forms
8. Share the app URL with field users — they log in and see only their org's forms

---

## Security Checklist Before Go-Live

- [ ] `FLASK_DEBUG=0` in production env vars
- [ ] `SECRET_KEY` is a random 32+ character string (never the default)
- [ ] `DATABASE_URL` uses `?sslmode=require`
- [ ] `.env` file is in `.gitignore` (never committed)
- [ ] Reset the Supabase database password if it was shared in chat/email
- [ ] Delete or disable the `/api/seed` endpoint after first use

---

## Disabling the Seed Endpoint

Once your real data is in, remove the seed endpoint so nobody can overwrite it.
In `app.py`, find and delete the entire `@app.route('/api/seed')` block (~80 lines).
