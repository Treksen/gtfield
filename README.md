# FieldOps v4 — Scalable ODK-like Field Data Collection Platform

## Roles
| Role | Capabilities |
|------|-------------|
| **Admin** | Full access: create/edit/delete everything, approve forms, manage orgs, edit any user |
| **Supervisor** | Create & edit forms (needs admin approval), view submissions, limited edits |
| **User** | Collect data using approved forms, manage own drafts/offline submissions |

---

## Quick Start (Local / SQLite)

```bash
pip install flask python-dotenv
python app.py
# Visit http://localhost:5000
# First registered user becomes admin
```

Seed demo data:
```bash
curl -X POST http://localhost:5000/api/seed
```

Demo credentials:
- Admin:      `admin@fieldops.demo` / `fieldops2024`
- Supervisor: `supervisor@fieldops.demo` / `fieldops2024`
- Field User: `amina@fieldops.demo` / `fieldops2024`

---

## Production Setup (Supabase PostgreSQL)

### 1. Create Supabase Project
1. Go to [supabase.com](https://supabase.com) → New Project
2. Note your **project ref**, **password**, and **region**

### 2. Get Connection String
- Supabase Dashboard → Project Settings → Database → **Connection string**
- Copy the **URI** format — it looks like:
  ```
  postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
  ```
- Append `?sslmode=require` at the end

### 3. Configure Environment
```bash
cp .env.example .env
# Edit .env and paste your DATABASE_URL
```

### 4. Install & Run
```bash
pip install -r requirements.txt
python app.py
```
Tables are created automatically on first run.

---

## What's New in v4

### Bug Fixes
- **Officer row click bug fixed** — clicking a table row no longer clears the list. Only View/Edit buttons trigger actions.

### Role System Simplified
- `admin` → full control
- `supervisor` → create/edit forms (pending admin approval), view all data
- `user` → field officer: collect data, manage own submissions

### Admin: Edit All User Details
- Admin can now edit name, role, phone, employee ID, organisation, **and zone** for any user from the Users page

### Supervisor Approval Workflow
- Supervisor creates/edits a form → status becomes `pending_approval`
- Admin sees in-app notification with **Approve / Reject** buttons right in the notification drawer
- On approval, dynamic DB table is created and form goes live
- Supervisor is notified of the outcome

### User (Field Officer) Collection Page
- **Available Forms** tab: filter by organisation and category
- Location auto-captured on page load, refreshes **every 30 minutes**
- **Drafts** tab: partially filled forms saved locally
- **Finalized** tab: completed forms saved while offline — click "Send" or "Sync Now" when back online
- **Sent** tab: successfully submitted forms
- Only **Draft** forms can be deleted

### Admin Organizations Page (`/admin/organizations`)
- Create, edit, delete organizations
- Click **View Details** to see all members, forms, and zones in one place
- Approve/reject pending forms directly from the org detail view

### Zone Management
- Zones are now admin-only to create (users select from admin-created zones)
- Zones can be scoped to a specific organization

### Database
- Full **PostgreSQL support via Supabase** — set `DATABASE_URL` in `.env`
- Falls back to **SQLite** for local development (no config needed)

---

## Form JSON Upload Format

```json
{
  "title": "Water Access Survey",
  "category": "water_sanitation",
  "description": "Monthly water survey",
  "schema": [
    {"label": "Community Name", "type": "text", "required": true},
    {"label": "Water Source", "type": "select", "options": ["Borehole", "River", "Piped"]},
    {"label": "Safe for Drinking", "type": "boolean", "required": true},
    {"label": "Distance (km)", "type": "number"}
  ]
}
```

Supported types: `text`, `textarea`, `number`, `integer`, `select`, `multiselect`, `boolean`, `date`, `datetime`, `time`, `gps`, `photo`, `barcode`, `range`, `email`, `phone`

---

## Key API Endpoints

| Method | Endpoint | Who |
|--------|----------|-----|
| POST | `/api/auth/register` | Public |
| POST | `/api/auth/login` | Public |
| GET | `/api/organizations` | All logged in |
| POST | `/api/organizations` | Admin |
| GET | `/api/forms` | All (filtered by role) |
| POST | `/api/forms` | Admin/Supervisor |
| POST | `/api/forms/:id/approve` | Admin |
| POST | `/api/forms/:id/reject` | Admin |
| POST | `/api/my/submissions` | User |
| GET | `/api/my/submissions?status=draft` | User |
| PUT | `/api/my/submissions/:id` | User |
| DELETE | `/api/my/submissions/:id` | User (drafts only) |
| GET | `/api/notifications` | All |
| PUT | `/api/notifications/read-all` | All |
| GET | `/api/admin/users` | Admin |
| PUT | `/api/admin/users/:id` | Admin |
