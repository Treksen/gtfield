"""
FieldOps v4 — Scalable ODK-like Field Data Collection Platform
Database: PostgreSQL via Supabase (falls back to SQLite for local dev)
Roles:    admin | supervisor | user (field officer)
"""
import os, hashlib, hmac, secrets, json, csv, io, re
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, request, jsonify, render_template,
                   session, redirect, url_for, g, Response)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ─── DATABASE ABSTRACTION (Supabase Postgres OR SQLite fallback) ─────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')   # e.g. postgresql://user:pass@host/db
USE_POSTGRES  = bool(DATABASE_URL)

def get_db():
    if 'db' not in g:
        if USE_POSTGRES:
            import psycopg2, psycopg2.extras
            g.db = psycopg2.connect(DATABASE_URL)
            g.db.autocommit = False
        else:
            import sqlite3
            path = os.path.join(os.path.dirname(__file__), 'instance', 'fieldops.db')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            g.db = sqlite3.connect(path)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
            g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db:
        try:
            db.close()
        except Exception:
            pass

def query(sql, args=(), one=False):
    db = get_db()
    if USE_POSTGRES:
        import psycopg2.extras
        import re as _re
        sql = sql.replace('?', '%s')
        sql = sql.replace("datetime('now')", "NOW()")
        sql = sql.replace("date('now')", "CURRENT_DATE")
        # Convert SQLite interval syntax to PostgreSQL
        # datetime('now','-N days/hours/minutes') -> NOW() - INTERVAL 'N days/hours/minutes'
        def _fix_interval(m):
            n, unit = m.group(1), m.group(2)
            if n.startswith('-'):
                return "NOW() - INTERVAL '" + n.lstrip('-') + " " + unit + "'"
            return "NOW() + INTERVAL '" + n + " " + unit + "'"
        import re as _re2
        sql = _re2.sub(r"datetime\('now',\s*'(-?\d+)\s+(days?|hours?|minutes?)'\)", _fix_interval, sql)
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rv = cur.fetchall()
        rows = [dict(r) for r in rv]
        return (rows[0] if rows else None) if one else rows
    else:
        cur = db.execute(sql, args)
        rv = cur.fetchall()
        if one:
            return dict(rv[0]) if rv else None
        return [dict(r) for r in rv]

def execute(sql, args=()):
    db = get_db()
    if USE_POSTGRES:
        import re as _re
        sql = sql.replace('?', '%s')
        sql = sql.replace("datetime('now')", "NOW()")
        sql = sql.replace("date('now')", "CURRENT_DATE")
        sql = sql.replace('AUTOINCREMENT', '')
        def _fix_interval(m):
            n, unit = m.group(1), m.group(2)
            if n.startswith('-'):
                return "NOW() - INTERVAL '" + n.lstrip('-') + " " + unit + "'"
            return "NOW() + INTERVAL '" + n + " " + unit + "'"
        import re as _re2
        sql = _re2.sub(r"datetime\('now',\s*'(-?\d+)\s+(days?|hours?|minutes?)'\)", _fix_interval, sql)
        with db.cursor() as cur:
            cur.execute(sql, args)
            try:
                lid = cur.fetchone()
                db.commit()
                return lid[0] if lid else None
            except Exception:
                db.commit()
                return None
    else:
        cur = db.execute(sql, args)
        db.commit()
        return cur.lastrowid

def execute_returning(sql, args=()):
    """INSERT ... RETURNING id — works on both backends."""
    db = get_db()
    if USE_POSTGRES:
        import re as _re
        sql = sql.replace('?', '%s')
        sql = sql.replace("datetime('now')", "NOW()")
        sql = sql.replace("date('now')", "CURRENT_DATE")
        sql = sql.replace('AUTOINCREMENT', '')
        def _fix_iv(m):
            n, unit = m.group(1), m.group(2)
            return ("NOW() - INTERVAL '" + n.lstrip('-') + " " + unit + "'"
                    if n.startswith('-')
                    else "NOW() + INTERVAL '" + n + " " + unit + "'")
        sql = _re.sub(r"datetime\('now',\s*'(-?\d+)\s+(days?|hours?|minutes?)'\)", _fix_iv, sql)
        if 'RETURNING' not in sql.upper():
            sql += ' RETURNING id'
        with db.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
            db.commit()
            return row[0] if row else None
    else:
        return execute(sql, args)

def executescript(sql):
    db = get_db()
    if USE_POSTGRES:
        stmts = [s.strip() for s in sql.split(';') if s.strip()]
        with db.cursor() as cur:
            for stmt in stmts:
                try:
                    cur.execute(stmt)
                except Exception as ex:
                    print(f"Schema stmt warning: {ex}")
        db.commit()
    else:
        db.executescript(sql)
        db.commit()

# ─── SCHEMA ──────────────────────────────────────────────────────────────────
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    full_name   TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','supervisor','user')),
    employee_id TEXT,
    phone       TEXT,
    org_id      INTEGER REFERENCES organizations(id),
    org_name    TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS zones (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    sub_county        TEXT,
    ward              TEXT,
    description       TEXT,
    target_households INTEGER DEFAULT 0,
    color             TEXT DEFAULT '#f0a500',
    bbox_north        REAL,
    bbox_south        REAL,
    bbox_east         REAL,
    bbox_west         REAL,
    is_active         INTEGER DEFAULT 1,
    org_id            INTEGER REFERENCES organizations(id),
    created_by        INTEGER REFERENCES users(id),
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS zone_assignments (
    id            SERIAL PRIMARY KEY,
    zone_id       INTEGER NOT NULL REFERENCES zones(id),
    officer_id    INTEGER NOT NULL REFERENCES users(id),
    target_visits INTEGER DEFAULT 10,
    assigned_by   INTEGER REFERENCES users(id),
    created_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE(zone_id, officer_id)
);
CREATE TABLE IF NOT EXISTS form_definitions (
    id           SERIAL PRIMARY KEY,
    org_id       INTEGER REFERENCES organizations(id),
    created_by   INTEGER REFERENCES users(id),
    title        TEXT NOT NULL,
    description  TEXT,
    category     TEXT DEFAULT 'general',
    version      INTEGER DEFAULT 1,
    status       TEXT DEFAULT 'draft' CHECK(status IN ('draft','pending_approval','approved','rejected','archived')),
    is_active    INTEGER DEFAULT 1,
    schema_json  TEXT NOT NULL,
    table_name   TEXT UNIQUE,
    rejection_note TEXT,
    approved_by  INTEGER REFERENCES users(id),
    approved_at  TIMESTAMP,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS form_submissions (
    id           SERIAL PRIMARY KEY,
    form_id      INTEGER NOT NULL REFERENCES form_definitions(id),
    officer_id   INTEGER NOT NULL REFERENCES users(id),
    zone_id      INTEGER REFERENCES zones(id),
    org_id       INTEGER REFERENCES organizations(id),
    latitude     REAL,
    longitude    REAL,
    status       TEXT DEFAULT 'sent' CHECK(status IN ('draft','finalized','sent')),
    submitted_at TIMESTAMP DEFAULT NOW(),
    synced_at    TIMESTAMP DEFAULT NOW(),
    data_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    ref_id      INTEGER,
    ref_type    TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS officer_locations (
    id          SERIAL PRIMARY KEY,
    officer_id  INTEGER NOT NULL REFERENCES users(id) UNIQUE,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    accuracy    REAL,
    battery_pct INTEGER,
    status      TEXT DEFAULT 'online',
    updated_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    officer_id  INTEGER REFERENCES users(id),
    alert_type  TEXT NOT NULL,
    severity    TEXT DEFAULT 'warning',
    message     TEXT NOT NULL,
    is_read     INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW()
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name   TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','supervisor','user')),
    employee_id TEXT,
    phone       TEXT,
    org_id      INTEGER REFERENCES organizations(id),
    org_name    TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS zones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    sub_county        TEXT,
    ward              TEXT,
    description       TEXT,
    target_households INTEGER DEFAULT 0,
    color             TEXT DEFAULT '#f0a500',
    bbox_north        REAL,
    bbox_south        REAL,
    bbox_east         REAL,
    bbox_west         REAL,
    is_active         INTEGER DEFAULT 1,
    org_id            INTEGER REFERENCES organizations(id),
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS zone_assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id       INTEGER NOT NULL REFERENCES zones(id),
    officer_id    INTEGER NOT NULL REFERENCES users(id),
    target_visits INTEGER DEFAULT 10,
    assigned_by   INTEGER REFERENCES users(id),
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(zone_id, officer_id)
);
CREATE TABLE IF NOT EXISTS form_definitions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id       INTEGER REFERENCES organizations(id),
    created_by   INTEGER REFERENCES users(id),
    title        TEXT NOT NULL,
    description  TEXT,
    category     TEXT DEFAULT 'general',
    version      INTEGER DEFAULT 1,
    status       TEXT DEFAULT 'draft' CHECK(status IN ('draft','pending_approval','approved','rejected','archived')),
    is_active    INTEGER DEFAULT 1,
    schema_json  TEXT NOT NULL,
    table_name   TEXT UNIQUE,
    rejection_note TEXT,
    approved_by  INTEGER REFERENCES users(id),
    approved_at  TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS form_submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id      INTEGER NOT NULL REFERENCES form_definitions(id),
    officer_id   INTEGER NOT NULL REFERENCES users(id),
    zone_id      INTEGER REFERENCES zones(id),
    org_id       INTEGER REFERENCES organizations(id),
    latitude     REAL,
    longitude    REAL,
    status       TEXT DEFAULT 'sent' CHECK(status IN ('draft','finalized','sent')),
    submitted_at TEXT DEFAULT (datetime('now')),
    synced_at    TEXT DEFAULT (datetime('now')),
    data_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    ref_id      INTEGER,
    ref_type    TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS officer_locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    officer_id  INTEGER NOT NULL REFERENCES users(id),
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    accuracy    REAL,
    battery_pct INTEGER,
    status      TEXT DEFAULT 'online',
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(officer_id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    officer_id  INTEGER REFERENCES users(id),
    alert_type  TEXT NOT NULL,
    severity    TEXT DEFAULT 'warning',
    message     TEXT NOT NULL,
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

def init_db():
    schema = PG_SCHEMA if USE_POSTGRES else SQLITE_SCHEMA
    executescript(schema)

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────
def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 260000)
    return f"{salt}${h.hex()}"

def check_pw(pw, stored):
    try:
        salt, h = stored.split('$')
        exp = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 260000)
        return hmac.compare_digest(h, exp.hex())
    except:
        return False

def login_required(roles=None):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Unauthorized'}), 401
                return redirect(url_for('login_page'))
            if roles:
                u = query("SELECT role FROM users WHERE id=?", [session['user_id']], one=True)
                if not u or u['role'] not in roles:
                    if request.path.startswith('/api/'):
                        return jsonify({'error': 'Forbidden'}), 403
                    return redirect(url_for('login_page'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def current_user():
    if 'user_id' in session:
        return query("SELECT * FROM users WHERE id=?", [session['user_id']], one=True)
    return None

def notify(user_id, ntype, title, message, ref_id=None, ref_type=None):
    try:
        execute_returning(
            "INSERT INTO notifications (user_id,type,title,message,ref_id,ref_type) VALUES (?,?,?,?,?,?)",
            [user_id, ntype, title, message, ref_id, ref_type]
        )
    except Exception as e:
        print(f"Notify error: {e}")

def notify_admins(ntype, title, message, ref_id=None, ref_type=None):
    admins = query("SELECT id FROM users WHERE role='admin' AND is_active=1")
    for a in admins:
        notify(a['id'], ntype, title, message, ref_id, ref_type)

# ─── DYNAMIC TABLE HELPERS ───────────────────────────────────────────────────
FIELD_TYPE_MAP = {
    'text': 'TEXT', 'textarea': 'TEXT', 'number': 'REAL', 'integer': 'INTEGER',
    'select': 'TEXT', 'multiselect': 'TEXT', 'boolean': 'INTEGER',
    'date': 'TEXT', 'datetime': 'TEXT', 'time': 'TEXT',
    'gps': 'TEXT', 'photo': 'TEXT', 'barcode': 'TEXT',
    'email': 'TEXT', 'phone': 'TEXT', 'range': 'REAL', 'url': 'TEXT',
}

def safe_col(name):
    s = re.sub(r'[^a-z0-9_]', '_', (name or 'field').lower().strip())
    return re.sub(r'_+', '_', s).strip('_') or 'field'

def slugify(text):
    return re.sub(r'_+', '_', re.sub(r'[^a-z0-9]+', '_', (text or '').lower().strip())).strip('_')

def create_dynamic_table(table_name, schema_fields):
    db = get_db()
    cols = [
        "id INTEGER PRIMARY KEY" + ("" if USE_POSTGRES else " AUTOINCREMENT"),
        "submission_id INTEGER REFERENCES form_submissions(id)",
        "officer_id INTEGER",
        "zone_id INTEGER",
        "org_id INTEGER",
        "submitted_at TEXT",
    ]
    if USE_POSTGRES:
        cols[0] = "id SERIAL PRIMARY KEY"
    for field in schema_fields:
        col = safe_col(field.get('name', field.get('label', 'field')))
        sql_type = FIELD_TYPE_MAP.get(field.get('type', 'text'), 'TEXT')
        cols.append(f'"{col}" {sql_type}')
    col_defs = ', '.join(cols)
    if USE_POSTGRES:
        with db.cursor() as cur:
            cur.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})')
        db.commit()
    else:
        db.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})')
        db.commit()

def insert_dynamic_row(table_name, schema_fields, data_dict, submission_id,
                        officer_id, zone_id, org_id, submitted_at):
    db = get_db()
    col_names = ['submission_id', 'officer_id', 'zone_id', 'org_id', 'submitted_at']
    values    = [submission_id, officer_id, zone_id, org_id, submitted_at]
    for field in schema_fields:
        col = safe_col(field.get('name', field.get('label', 'field')))
        val = data_dict.get(field.get('name', field.get('label', '')))
        if isinstance(val, (list, dict)):
            val = json.dumps(val)
        col_names.append(f'"{col}"')
        values.append(val)
    placeholders = ', '.join(['%s' if USE_POSTGRES else '?'] * len(values))
    col_str = ', '.join(col_names)
    sql = f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholders})'
    if USE_POSTGRES:
        with db.cursor() as cur:
            cur.execute(sql, values)
        db.commit()
    else:
        db.execute(sql, values)
        db.commit()

# ─── PAGE ROUTES ──────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    u = current_user()
    if u['role'] == 'user':
        return redirect(url_for('collect_page'))
    return redirect(url_for('dashboard_page'))

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/dashboard')
@login_required(roles=['admin', 'supervisor'])
def dashboard_page():
    return render_template('dashboard.html', user=current_user())

@app.route('/officers')
@login_required(roles=['admin', 'supervisor'])
def officers_page():
    return render_template('officers.html', user=current_user())

@app.route('/zones')
@login_required(roles=['admin', 'supervisor'])
def zones_page():
    return render_template('zones.html', user=current_user())

@app.route('/forms')
@login_required(roles=['admin', 'supervisor'])
def forms_page():
    return render_template('forms.html', user=current_user())

@app.route('/forms/builder')
@login_required(roles=['admin', 'supervisor'])
def form_builder_page():
    return render_template('form_builder.html', user=current_user())


@app.route('/forms/<int:fid>/review')
@login_required(roles=['admin'])
def form_review_page(fid):
    form = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not form:
        return redirect(url_for('forms_page'))
    return render_template('form_review.html', user=current_user(), form=form)

@app.route('/forms/<int:fid>/data')
@login_required(roles=['admin', 'supervisor'])
def form_data_page(fid):
    form = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not form:
        return redirect(url_for('forms_page'))
    return render_template('form_data.html', user=current_user(), form=form)

@app.route('/reports')
@login_required(roles=['admin', 'supervisor'])
def reports_page():
    return render_template('reports.html', user=current_user())

@app.route('/alerts')
@login_required(roles=['admin', 'supervisor'])
def alerts_page():
    return render_template('alerts.html', user=current_user())

@app.route('/admin/users')
@login_required(roles=['admin'])
def admin_users_page():
    return render_template('admin_users.html', user=current_user())

@app.route('/admin/organizations')
@login_required(roles=['admin'])
def admin_orgs_page():
    return render_template('admin_orgs.html', user=current_user())

@app.route('/profile')
@login_required()
def profile_page():
    return render_template('profile.html', user=current_user())

# ── USER (field officer) pages ────────────────────────────────────────────────
@app.route('/collect')
@login_required(roles=['user'])
def collect_page():
    return render_template('collect.html', user=current_user())

# ─── AUTH API ─────────────────────────────────────────────────────────────────
@app.route('/api/auth/register', methods=['POST'])
def api_register():
    d = request.json or {}
    name  = (d.get('full_name') or '').strip()
    email = (d.get('email') or '').strip().lower()
    pw    = d.get('password') or ''
    if not name or not email or not pw:
        return jsonify({'error': 'Name, email and password required'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if query("SELECT id FROM users WHERE email=?", [email], one=True):
        return jsonify({'error': 'Email already registered'}), 409
    count = query("SELECT COUNT(*) as c FROM users", one=True)['c']
    role  = 'admin' if count == 0 else 'user'
    uid   = execute_returning(
        "INSERT INTO users (full_name,email,password,role,phone,org_name) VALUES (?,?,?,?,?,?)",
        [name, email, hash_pw(pw), role, d.get('phone',''), d.get('org_name','')]
    )
    session['user_id']   = uid
    session['user_role'] = role
    return jsonify({'user': {'id': uid, 'full_name': name, 'email': email, 'role': role}})

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    d     = request.json or {}
    email = (d.get('email') or '').strip().lower()
    pw    = d.get('password') or ''
    u     = query("SELECT * FROM users WHERE email=? AND is_active=1", [email], one=True)
    if not u or not check_pw(pw, u['password']):
        return jsonify({'error': 'Invalid email or password'}), 401
    session['user_id']   = u['id']
    session['user_role'] = u['role']
    # Mark user as online (non-fatal)
    try:
        execute("""INSERT INTO officer_locations (officer_id,latitude,longitude,status,updated_at)
                   VALUES (?,0,0,'online',datetime('now'))
                   ON CONFLICT(officer_id) DO UPDATE SET status='online',updated_at=datetime('now')""", [u['id']])
    except Exception as e:
        print(f"Login location error: {e}")
    return jsonify({'user': {'id': u['id'], 'full_name': u['full_name'],
                             'email': u['email'], 'role': u['role'],
                             'org_id': u['org_id'], 'org_name': u['org_name']}})

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    uid = session.get('user_id')
    if uid:
        execute("UPDATE officer_locations SET status='offline',updated_at=datetime('now') WHERE officer_id=?", [uid])
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/heartbeat', methods=['POST'])
@login_required()
def api_heartbeat():
    """Called every 60s from browser to keep status=online."""
    try:
        execute("""INSERT INTO officer_locations (officer_id,latitude,longitude,status,updated_at)
                   VALUES (?,0,0,'online',datetime('now'))
                   ON CONFLICT(officer_id) DO UPDATE SET status='online',updated_at=datetime('now')""",
                [session['user_id']])
    except Exception as e:
        print(f"Heartbeat error: {e}")
        return jsonify({'ok': True, 'warning': str(e)})
    return jsonify({'ok': True})

@app.route('/api/auth/me')
@login_required()
def api_me():
    return jsonify(current_user())

@app.route('/api/auth/profile', methods=['PUT'])
@login_required()
def api_update_profile():
    d = request.json or {}
    execute("UPDATE users SET full_name=?,phone=?,org_name=? WHERE id=?",
            [d.get('full_name'), d.get('phone'), d.get('org_name'), session['user_id']])
    return jsonify({'message': 'Profile updated'})

@app.route('/api/auth/password', methods=['PUT'])
@login_required()
def api_change_password():
    d = request.json or {}
    u = query("SELECT password FROM users WHERE id=?", [session['user_id']], one=True)
    if not check_pw(d.get('current_password', ''), u['password']):
        return jsonify({'error': 'Current password incorrect'}), 401
    if len(d.get('new_password', '')) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    execute("UPDATE users SET password=? WHERE id=?",
            [hash_pw(d['new_password']), session['user_id']])
    return jsonify({'message': 'Password updated'})

# ─── NOTIFICATIONS API ────────────────────────────────────────────────────────
@app.route('/api/notifications')
@login_required()
def api_notifications():
    rows = query("""
        SELECT * FROM notifications WHERE user_id=?
        ORDER BY created_at DESC LIMIT 50
    """, [session['user_id']])
    return jsonify(rows)

@app.route('/api/notifications/unread-count')
@login_required()
def api_notif_count():
    r = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0",
              [session['user_id']], one=True)
    return jsonify({'count': r['c'] if r else 0})

@app.route('/api/notifications/<int:nid>/read', methods=['PUT'])
@login_required()
def api_notif_read(nid):
    execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
            [nid, session['user_id']])
    return jsonify({'ok': True})

@app.route('/api/notifications/read-all', methods=['PUT'])
@login_required()
def api_notif_read_all():
    execute("UPDATE notifications SET is_read=1 WHERE user_id=?", [session['user_id']])
    return jsonify({'ok': True})

# ─── ADMIN — ORGANIZATIONS ────────────────────────────────────────────────────
@app.route('/api/organizations')
@login_required()
def api_orgs():
    u = current_user()
    if u['role'] == 'supervisor' and u.get('org_id'):
        rows = query("""
            SELECT o.*,
              (SELECT COUNT(*) FROM users u2 WHERE u2.org_id=o.id AND u2.is_active=1) as member_count,
              (SELECT COUNT(*) FROM form_definitions f WHERE f.org_id=o.id AND f.is_active=1) as form_count,
              (SELECT COUNT(*) FROM zones z WHERE z.org_id=o.id AND z.is_active=1) as zone_count
            FROM organizations o WHERE o.id=? ORDER BY o.name
        """, [u['org_id']])
    else:
        rows = query("""
            SELECT o.*,
              (SELECT COUNT(*) FROM users u2 WHERE u2.org_id=o.id AND u2.is_active=1) as member_count,
              (SELECT COUNT(*) FROM form_definitions f WHERE f.org_id=o.id AND f.is_active=1) as form_count,
              (SELECT COUNT(*) FROM zones z WHERE z.org_id=o.id AND z.is_active=1) as zone_count
            FROM organizations o ORDER BY o.name
        """)
    return jsonify(rows)

@app.route('/api/organizations', methods=['POST'])
@login_required(roles=['admin'])
def api_create_org():
    d    = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    slug = slugify(name)
    try:
        oid = execute_returning(
            "INSERT INTO organizations (name,slug,description) VALUES (?,?,?)",
            [name, slug, d.get('description', '')]
        )
        return jsonify({'id': oid, 'message': 'Organization created'})
    except Exception as e:
        return jsonify({'error': 'Name already exists'}), 409

@app.route('/api/organizations/<int:oid>', methods=['PUT'])
@login_required(roles=['admin'])
def api_update_org(oid):
    d = request.json or {}
    execute("UPDATE organizations SET name=?,description=? WHERE id=?",
            [d.get('name'), d.get('description'), oid])
    return jsonify({'message': 'Updated'})

@app.route('/api/organizations/<int:oid>', methods=['DELETE'])
@login_required(roles=['admin'])
def api_delete_org(oid):
    execute("DELETE FROM organizations WHERE id=?", [oid])
    return jsonify({'message': 'Deleted'})

@app.route('/api/organizations/<int:oid>/members')
@login_required(roles=['admin', 'supervisor'])
def api_org_members(oid):
    rows = query("""
        SELECT id,full_name,email,role,phone,employee_id,org_name,is_active,created_at
        FROM users WHERE org_id=? ORDER BY role,full_name
    """, [oid])
    return jsonify(rows)

# ─── ADMIN — USERS ────────────────────────────────────────────────────────────
@app.route('/api/admin/users')
@login_required(roles=['admin'])
def api_admin_users():
    rows = query("""
        SELECT u.id,u.full_name,u.email,u.role,u.phone,u.org_name,
               u.employee_id,u.is_active,u.created_at,u.org_id,
               o.name as org_name_display
        FROM users u LEFT JOIN organizations o ON o.id=u.org_id
        ORDER BY u.created_at DESC
    """)
    return jsonify(rows)

@app.route('/api/admin/users', methods=['POST'])
@login_required(roles=['admin'])
def api_admin_create_user():
    d     = request.json or {}
    name  = (d.get('full_name') or '').strip()
    email = (d.get('email') or '').strip().lower()
    role  = d.get('role') or 'user'
    if role not in ('admin', 'supervisor', 'user'):
        return jsonify({'error': 'Invalid role'}), 400
    if not name or not email:
        return jsonify({'error': 'Name and email required'}), 400
    if query("SELECT id FROM users WHERE email=?", [email], one=True):
        return jsonify({'error': 'Email already exists'}), 409
    uid = execute_returning(
        "INSERT INTO users (full_name,email,password,role,phone,org_name,employee_id,org_id) VALUES (?,?,?,?,?,?,?,?)",
        [name, email, hash_pw(d.get('password','fieldops2024')), role,
         d.get('phone',''), d.get('org_name',''), d.get('employee_id',''),
         d.get('org_id') or None]
    )
    return jsonify({'id': uid, 'message': 'User created'})

@app.route('/api/admin/users/<int:uid>', methods=['PUT'])
@login_required(roles=['admin'])
def api_admin_update_user(uid):
    d = request.json or {}
    # Auto-resolve org_name from org_id if not explicitly provided
    org_id   = d.get('org_id') or None
    org_name = d.get('org_name') or ''
    if org_id and not org_name:
        org = query("SELECT name FROM organizations WHERE id=?", [org_id], one=True)
        if org:
            org_name = org['name']
    execute("""UPDATE users SET full_name=?,role=?,phone=?,employee_id=?,
               org_name=?,org_id=?,is_active=? WHERE id=?""",
            [d.get('full_name'), d.get('role'), d.get('phone'),
             d.get('employee_id'), org_name,
             org_id, d.get('is_active', 1), uid])
    # Zone assignment if provided
    zone_id = d.get('zone_id')
    if zone_id:
        execute("INSERT INTO zone_assignments (zone_id,officer_id,target_visits,assigned_by) VALUES (?,?,?,?) ON CONFLICT(zone_id,officer_id) DO UPDATE SET target_visits=EXCLUDED.target_visits,assigned_by=EXCLUDED.assigned_by",
                [zone_id, uid, d.get('target_visits', 10), session['user_id']])
    elif zone_id == 0:
        execute("DELETE FROM zone_assignments WHERE officer_id=?", [uid])
    return jsonify({'message': 'Updated'})

@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@login_required(roles=['admin'])
def api_admin_delete_user(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    execute("UPDATE users SET is_active=0 WHERE id=?", [uid])
    return jsonify({'message': 'Deactivated'})

# ─── OFFICERS / USERS LIST ────────────────────────────────────────────────────
@app.route('/api/officers')
@login_required(roles=['admin', 'supervisor'])
def api_officers():
    rows = query("""
        SELECT u.id, u.full_name, u.email, u.phone, u.employee_id,
               COALESCE(og.name, u.org_name, '—') as org_name,
               u.is_active, u.created_at, u.org_id,
               ol.status as location_status, ol.battery_pct,
               ol.updated_at as last_seen, ol.latitude, ol.longitude,
               (SELECT COUNT(*) FROM form_submissions s
                WHERE s.officer_id=u.id AND date(s.submitted_at)=date('now')
                AND s.status='sent') as visits_today,
               (SELECT z.name FROM zone_assignments za
                JOIN zones z ON z.id=za.zone_id
                WHERE za.officer_id=u.id LIMIT 1) as zone_name,
               (SELECT za.zone_id FROM zone_assignments za WHERE za.officer_id=u.id LIMIT 1) as zone_id
        FROM users u
        LEFT JOIN officer_locations ol ON ol.officer_id=u.id
        LEFT JOIN organizations og ON og.id=u.org_id
        WHERE u.role IN ('user','supervisor') AND u.is_active=1
        ORDER BY u.role, u.full_name
    """)
    curr = current_user()
    if curr['role'] == 'supervisor' and curr.get('org_id'):
        rows = [r for r in rows if r.get('org_id') == curr['org_id']]
    return jsonify(rows)

@app.route('/api/officers/<int:oid>')
@login_required(roles=['admin', 'supervisor'])
def api_officer_detail(oid):
    officer = query("SELECT * FROM users WHERE id=? AND role='user'", [oid], one=True)
    if not officer:
        return jsonify({'error': 'Not found'}), 404
    stats = query("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN date(submitted_at)=date('now') THEN 1 END) as today,
            COUNT(CASE WHEN submitted_at>=datetime('now','-7 days') THEN 1 END) as week,
            COUNT(CASE WHEN submitted_at>=datetime('now','-30 days') THEN 1 END) as month
        FROM form_submissions WHERE officer_id=? AND status='sent'
    """, [oid], one=True)
    zone = query("""
        SELECT z.id, z.name FROM zone_assignments za
        JOIN zones z ON z.id=za.zone_id WHERE za.officer_id=? LIMIT 1
    """, [oid], one=True)
    return jsonify({'officer': officer, 'stats': stats or {}, 'zone': zone})

# ─── ZONES ────────────────────────────────────────────────────────────────────
@app.route('/api/zones')
@login_required()
def api_zones():
    u = current_user()
    if u['role'] == 'admin':
        rows = query("""
            SELECT z.*, u.full_name as creator_name,
                   o.name as org_name_display,
                   COUNT(DISTINCT za.officer_id) as officer_count
            FROM zones z
            LEFT JOIN users u ON u.id=z.created_by
            LEFT JOIN organizations o ON o.id=z.org_id
            LEFT JOIN zone_assignments za ON za.zone_id=z.id
            WHERE z.is_active=1
            GROUP BY z.id, z.name, z.sub_county, z.ward, z.description, z.target_households, z.color, z.bbox_north, z.bbox_south, z.bbox_east, z.bbox_west, z.is_active, z.org_id, z.created_by, z.created_at, u.full_name, o.name
            ORDER BY z.name
        """)
    elif u['role'] == 'supervisor':
        rows = query("""
            SELECT z.*, u.full_name as creator_name,
                   o.name as org_name_display,
                   COUNT(DISTINCT za.officer_id) as officer_count
            FROM zones z
            LEFT JOIN users u ON u.id=z.created_by
            LEFT JOIN organizations o ON o.id=z.org_id
            LEFT JOIN zone_assignments za ON za.zone_id=z.id
            WHERE z.is_active=1 AND (z.org_id=? OR z.org_id IS NULL)
            GROUP BY z.id, z.name, z.sub_county, z.ward, z.description, z.target_households, z.color, z.bbox_north, z.bbox_south, z.bbox_east, z.bbox_west, z.is_active, z.org_id, z.created_by, z.created_at, u.full_name, o.name
            ORDER BY z.name
        """, [u.get('org_id') or 0])
    else:
        # Users only see zones of their org
        rows = query("""
            SELECT z.*, o.name as org_name_display
            FROM zones z
            LEFT JOIN organizations o ON o.id=z.org_id
            WHERE z.is_active=1 AND (z.org_id=? OR z.org_id IS NULL)
            ORDER BY z.name
        """, [u.get('org_id') or 0])
    return jsonify(rows)

@app.route('/api/zones', methods=['POST'])
@login_required(roles=['admin', 'supervisor'])
def api_create_zone():
    d = request.json or {}
    if not d.get('name'):
        return jsonify({'error': 'Zone name required'}), 400
    u = current_user()
    org_id = d.get('org_id') or u.get('org_id') or None
    zid = execute_returning(
        """INSERT INTO zones (name,sub_county,ward,description,target_households,
           color,bbox_north,bbox_south,bbox_east,bbox_west,org_id,created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [d['name'], d.get('sub_county'), d.get('ward'), d.get('description'),
         d.get('target_households', 0), d.get('color', '#f0a500'),
         d.get('bbox_north'), d.get('bbox_south'), d.get('bbox_east'),
         d.get('bbox_west'), org_id, session['user_id']]
    )
    return jsonify({'id': zid, 'message': 'Zone created'})

@app.route('/api/zones/<int:zid>', methods=['PUT'])
@login_required(roles=['admin', 'supervisor'])
def api_update_zone(zid):
    d = request.json or {}
    execute("""UPDATE zones SET name=?,sub_county=?,ward=?,description=?,
               target_households=?,color=?,org_id=? WHERE id=?""",
            [d.get('name'), d.get('sub_county'), d.get('ward'), d.get('description'),
             d.get('target_households', 0), d.get('color', '#f0a500'),
             d.get('org_id') or None, zid])
    return jsonify({'message': 'Updated'})

@app.route('/api/zones/<int:zid>', methods=['DELETE'])
@login_required(roles=['admin'])
def api_delete_zone(zid):
    execute("UPDATE zones SET is_active=0 WHERE id=?", [zid])
    return jsonify({'message': 'Deactivated'})

@app.route('/api/zones/<int:zid>/assign', methods=['POST'])
@login_required(roles=['admin', 'supervisor'])
def api_assign_zone(zid):
    d = request.json or {}
    execute("INSERT INTO zone_assignments (zone_id,officer_id,target_visits,assigned_by) VALUES (?,?,?,?) ON CONFLICT(zone_id,officer_id) DO UPDATE SET target_visits=EXCLUDED.target_visits,assigned_by=EXCLUDED.assigned_by",
            [zid, d['officer_id'], d.get('target_visits', 10), session['user_id']])
    return jsonify({'message': 'Assigned'})

@app.route('/api/zones/<int:zid>/unassign/<int:oid>', methods=['DELETE'])
@login_required(roles=['admin'])
def api_unassign_zone(zid, oid):
    execute("DELETE FROM zone_assignments WHERE zone_id=? AND officer_id=?", [zid, oid])
    return jsonify({'message': 'Unassigned'})

# ─── FORMS ────────────────────────────────────────────────────────────────────
@app.route('/api/forms')
@login_required()
def api_forms():
    u = current_user()
    if u['role'] == 'admin':
        rows = query("""
            SELECT f.*, o.name as org_name_display, u.full_name as creator_name
            FROM form_definitions f
            LEFT JOIN organizations o ON o.id=f.org_id
            LEFT JOIN users u ON u.id=f.created_by
            ORDER BY f.created_at DESC
        """)
    elif u['role'] == 'supervisor':
        rows = query("""
            SELECT f.*, o.name as org_name_display, u2.full_name as creator_name
            FROM form_definitions f
            LEFT JOIN organizations o ON o.id=f.org_id
            LEFT JOIN users u2 ON u2.id=f.created_by
            WHERE (f.org_id=? OR f.created_by=?)
            ORDER BY f.created_at DESC
        """, [u.get('org_id') or -1, u['id']])

    else:
        # Users only see approved active forms for their org
        rows = query("""
            SELECT f.id, f.title, f.description, f.category, f.schema_json,
                   f.status, f.is_active, f.org_id, o.name as org_name_display
            FROM form_definitions f
            LEFT JOIN organizations o ON o.id=f.org_id
            WHERE f.status='approved' AND f.is_active=1
              AND (f.org_id=? OR f.org_id IS NULL)
            ORDER BY f.title
        """, [u.get('org_id') or 0])

    result = []
    for f in rows:
        fd = dict(f)
        fd['schema'] = json.loads(f['schema_json']) if f['schema_json'] else []
        sc = query("SELECT COUNT(*) as c FROM form_submissions WHERE form_id=?", [f['id']], one=True)
        fd['submission_count'] = sc['c'] if sc else 0
        result.append(fd)
    return jsonify(result)

@app.route('/api/forms', methods=['POST'])
@login_required(roles=['admin', 'supervisor'])
def api_create_form():
    d      = request.json or {}
    title  = (d.get('title') or '').strip()
    schema = d.get('schema', [])
    if not title:
        return jsonify({'error': 'Form title required'}), 400
    if not schema:
        return jsonify({'error': 'Form must have at least one field'}), 400
    for i, field in enumerate(schema):
        if not field.get('label'):
            return jsonify({'error': f'Field {i+1} missing label'}), 400
        if not field.get('name'):
            field['name'] = safe_col(field['label'])
        if not field.get('type'):
            field['type'] = 'text'

    u = current_user()
    # Supervisors create in pending state; admins auto-approve
    status = 'approved' if u['role'] == 'admin' else 'pending_approval'

    base = f"form_{slugify(title)}"
    table_name = base
    suffix = 1
    existing_check = "SELECT name FROM sqlite_master WHERE type='table' AND name=?" if not USE_POSTGRES else "SELECT tablename FROM pg_tables WHERE tablename=%s"
    while True:
        chk = query(existing_check.replace('?', '%s' if USE_POSTGRES else '?'), [table_name], one=True)
        if not chk:
            break
        table_name = f"{base}_{suffix}"
        suffix += 1

    fid = execute_returning(
        """INSERT INTO form_definitions
           (org_id,created_by,title,description,category,schema_json,table_name,status,is_active)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [d.get('org_id') or u.get('org_id'), u['id'], title,
         d.get('description'), d.get('category', 'general'),
         json.dumps(schema), table_name, status,
         1 if u['role'] == 'admin' else 0]
    )

    if u['role'] == 'admin':
        try:
            create_dynamic_table(table_name, schema)
        except Exception as e:
            execute("DELETE FROM form_definitions WHERE id=?", [fid])
            return jsonify({'error': f'Failed to create data table: {e}'}), 500
    else:
        # Notify admins to approve
        notify_admins(
            'form_approval_request',
            f'Form Approval: {title}',
            f'{u["full_name"]} submitted "{title}" for approval.',
            ref_id=fid, ref_type='form'
        )

    return jsonify({'id': fid, 'status': status,
                    'message': 'Form created' if u['role'] == 'admin' else 'Form submitted for admin approval'})

@app.route('/api/forms/upload', methods=['POST'])
@login_required(roles=['admin', 'supervisor'])
def api_upload_form():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f        = request.files['file']
    filename = f.filename.lower()
    u        = current_user()
    try:
        if filename.endswith('.json'):
            data = json.load(f)
            if isinstance(data, dict):
                title  = data.get('title') or data.get('name') or 'Uploaded Form'
                cat    = data.get('category', 'general')
                desc   = data.get('description', '')
                raw    = data.get('schema') or data.get('fields') or data.get('questions') or []
            else:
                title, cat, desc, raw = 'Uploaded Form', 'general', '', data
            schema = [{'name': safe_col(x.get('name') or x.get('label') or 'field'),
                       'label': x.get('label') or x.get('name') or 'Field',
                       'type': x.get('type') or x.get('field_type') or 'text',
                       'required': x.get('required', False),
                       'options': x.get('options') or x.get('choices') or [],
                       'hint': x.get('hint') or ''} for x in raw]
        elif filename.endswith('.csv'):
            content = f.read().decode('utf-8')
            reader  = csv.DictReader(io.StringIO(content))
            fields  = [c for c in (reader.fieldnames or []) if c.lower() not in ('id','created_at','updated_at')]
            title   = filename.replace('.csv','').replace('_',' ').title()
            cat, desc = 'general', f'Imported from {filename}'
            schema  = [{'name': safe_col(c), 'label': c.replace('_',' ').title(),
                        'type': 'text', 'required': False, 'options': [], 'hint': ''} for c in fields]
        else:
            return jsonify({'error': 'Only .json and .csv files supported'}), 400

        if not schema:
            return jsonify({'error': 'No fields found in file'}), 400

        status = 'approved' if u['role'] == 'admin' else 'pending_approval'
        base = f"form_{slugify(title)}"
        table_name, suffix = base, 1
        existing_check = "SELECT name FROM sqlite_master WHERE type='table' AND name=?" if not USE_POSTGRES else "SELECT tablename FROM pg_tables WHERE tablename=%s"
        while query(existing_check.replace('?', '%s' if USE_POSTGRES else '?'), [table_name], one=True):
            table_name = f"{base}_{suffix}"; suffix += 1

        fid = execute_returning(
            "INSERT INTO form_definitions (created_by,org_id,title,description,category,schema_json,table_name,status,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
            [u['id'], u.get('org_id'), title, desc, cat, json.dumps(schema), table_name, status,
             1 if u['role'] == 'admin' else 0]
        )
        if u['role'] == 'admin':
            create_dynamic_table(table_name, schema)
        else:
            notify_admins('form_approval_request', f'Form Approval: {title}',
                          f'{u["full_name"]} uploaded "{title}" for approval.',
                          ref_id=fid, ref_type='form')

        return jsonify({'id': fid, 'title': title, 'field_count': len(schema),
                        'status': status, 'message': 'Form imported'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/forms/<int:fid>', methods=['GET'])
@login_required()
def api_form_detail(fid):
    f = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not f:
        return jsonify({'error': 'Not found'}), 404
    fd = dict(f)
    fd['schema'] = json.loads(f['schema_json']) if f['schema_json'] else []
    return jsonify(fd)

@app.route('/api/forms/<int:fid>/approve', methods=['POST'])
@login_required(roles=['admin'])
def api_approve_form(fid):
    f = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not f:
        return jsonify({'error': 'Not found'}), 404
    schema = json.loads(f['schema_json'])
    try:
        create_dynamic_table(f['table_name'], schema)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    execute("UPDATE form_definitions SET status='approved',is_active=1,approved_by=?,approved_at=datetime('now') WHERE id=?",
            [session['user_id'], fid])
    notify(f['created_by'], 'form_approved', 'Form Approved',
           f'Your form "{f["title"]}" has been approved and is now live.', ref_id=fid, ref_type='form')
    return jsonify({'message': 'Approved'})

@app.route('/api/forms/<int:fid>/reject', methods=['POST'])
@login_required(roles=['admin'])
def api_reject_form(fid):
    f = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not f:
        return jsonify({'error': 'Not found'}), 404
    note = (request.json or {}).get('note', '')
    execute("UPDATE form_definitions SET status='rejected',rejection_note=? WHERE id=?", [note, fid])
    notify(f['created_by'], 'form_rejected', 'Form Rejected',
           f'Your form "{f["title"]}" was rejected. Note: {note}', ref_id=fid, ref_type='form')
    return jsonify({'message': 'Rejected'})

@app.route('/api/forms/<int:fid>', methods=['PUT'])
@login_required(roles=['admin', 'supervisor'])
def api_update_form(fid):
    f = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not f:
        return jsonify({'error': 'Not found'}), 404
    d = request.json or {}
    u = current_user()
    if u['role'] == 'supervisor' and f['created_by'] != u['id']:
        return jsonify({'error': 'You can only edit forms you created'}), 403
    # Supervisor edits go back to pending
    new_status = f['status']
    if u['role'] == 'supervisor':
        new_status = 'pending_approval'
        notify_admins('form_approval_request', f'Form Edit Approval: {f["title"]}',
                      f'{u["full_name"]} edited "{f["title"]}" — awaiting re-approval.',
                      ref_id=fid, ref_type='form')
    execute("UPDATE form_definitions SET title=?,description=?,category=?,is_active=?,status=?,updated_at=datetime('now') WHERE id=?",
            [d.get('title', f['title']), d.get('description', f['description']),
             d.get('category', f['category']), d.get('is_active', f['is_active']),
             new_status, fid])
    return jsonify({'message': 'Updated', 'status': new_status})

@app.route('/api/forms/<int:fid>', methods=['DELETE'])
@login_required(roles=['admin'])
def api_delete_form(fid):
    execute("UPDATE form_definitions SET is_active=0,status='archived' WHERE id=?", [fid])
    return jsonify({'message': 'Archived'})

@app.route('/api/forms/<int:fid>/stats')
@login_required(roles=['admin', 'supervisor'])
def api_form_stats(fid):
    s = query("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN date(submitted_at)=date('now') THEN 1 END) as today,
               COUNT(CASE WHEN submitted_at>=datetime('now','-7 days') THEN 1 END) as week,
               COUNT(DISTINCT officer_id) as unique_officers,
               COUNT(DISTINCT zone_id) as unique_zones
        FROM form_submissions WHERE form_id=? AND status='sent'
    """, [fid], one=True)
    return jsonify(s or {})

@app.route('/api/forms/<int:fid>/submissions')
@login_required(roles=['admin', 'supervisor'])
def api_form_submissions(fid):
    limit  = int(request.args.get('limit', 200))
    offset = int(request.args.get('offset', 0))
    rows = query("""
        SELECT fs.*, u.full_name as officer_name, z.name as zone_name
        FROM form_submissions fs
        JOIN users u ON u.id=fs.officer_id
        LEFT JOIN zones z ON z.id=fs.zone_id
        WHERE fs.form_id=? AND fs.status='sent'
        ORDER BY fs.submitted_at DESC LIMIT ? OFFSET ?
    """, [fid, limit, offset])
    result = []
    for r in rows:
        rd = dict(r)
        rd['data'] = json.loads(r['data_json']) if r['data_json'] else {}
        result.append(rd)
    return jsonify(result)

@app.route('/api/forms/<int:fid>/submissions/export')
@login_required(roles=['admin', 'supervisor'])
def api_export_submissions(fid):
    form   = query("SELECT * FROM form_definitions WHERE id=?", [fid], one=True)
    if not form:
        return jsonify({'error': 'Not found'}), 404
    schema = json.loads(form['schema_json'])
    fmt    = request.args.get('format', 'csv')
    rows   = query("""
        SELECT fs.*, u.full_name as officer_name, z.name as zone_name
        FROM form_submissions fs
        JOIN users u ON u.id=fs.officer_id
        LEFT JOIN zones z ON z.id=fs.zone_id
        WHERE fs.form_id=? AND fs.status='sent'
        ORDER BY fs.submitted_at DESC
    """, [fid])
    if fmt == 'json':
        result = []
        for r in rows:
            rd = dict(r); rd['data'] = json.loads(r['data_json'] or '{}'); del rd['data_json']
            result.append(rd)
        return Response(json.dumps(result, indent=2, default=str), mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{form["title"]}.json"'})
    out = io.StringIO()
    fn  = ['id','submitted_at','officer_name','zone_name','latitude','longitude'] + [f['label'] for f in schema]
    w   = csv.DictWriter(out, fieldnames=fn, extrasaction='ignore')
    w.writeheader()
    for r in rows:
        rd = {k: r[k] for k in ['id','submitted_at','officer_name','zone_name','latitude','longitude']}
        data = json.loads(r['data_json'] or '{}')
        for field in schema:
            rd[field['label']] = data.get(field['name'], '')
        w.writerow(rd)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{form["title"]}.csv"'})

# ─── USER (FIELD OFFICER) SUBMISSIONS ────────────────────────────────────────
@app.route('/api/my/submissions')
@login_required()
def api_my_submissions():
    status = request.args.get('status')  # draft | finalized | sent
    sql = """
        SELECT fs.id, fs.form_id, fs.status, fs.submitted_at, fs.data_json,
               fs.zone_id, fs.latitude, fs.longitude,
               f.title as form_title, z.name as zone_name
        FROM form_submissions fs
        JOIN form_definitions f ON f.id=fs.form_id
        LEFT JOIN zones z ON z.id=fs.zone_id
        WHERE fs.officer_id=?
    """
    args = [session['user_id']]
    if status:
        sql += " AND fs.status=?"; args.append(status)
    sql += " ORDER BY fs.submitted_at DESC LIMIT 100"
    rows = query(sql, args)
    result = []
    for r in rows:
        rd = dict(r); rd['data'] = json.loads(r['data_json'] or '{}')
        result.append(rd)
    return jsonify(result)

@app.route('/api/my/submissions', methods=['POST'])
@login_required()
def api_my_submit():
    d       = request.json or {}
    fid     = d.get('form_id')
    status  = d.get('status', 'sent')  # draft | finalized | sent
    if status not in ('draft','finalized','sent'):
        status = 'sent'
    form = query("SELECT * FROM form_definitions WHERE id=? AND status='approved' AND is_active=1", [fid], one=True)
    if not form:
        return jsonify({'error': 'Form not found or not approved'}), 404
    data    = d.get('data', {})
    schema  = json.loads(form['schema_json'])
    u       = current_user()
    zone_id = d.get('zone_id')
    lat     = d.get('latitude')
    lng     = d.get('longitude')
    now     = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    sid = execute_returning(
        "INSERT INTO form_submissions (form_id,officer_id,zone_id,org_id,latitude,longitude,status,submitted_at,data_json) VALUES (?,?,?,?,?,?,?,?,?)",
        [fid, u['id'], zone_id, u.get('org_id'), lat, lng, status, now, json.dumps(data)]
    )
    if status == 'sent':
        try:
            insert_dynamic_row(form['table_name'], schema, data, sid, u['id'], zone_id, u.get('org_id'), now)
        except Exception as e:
            print(f"Dynamic row insert error: {e}")
    # Update location
    execute("""INSERT INTO officer_locations
               (officer_id,latitude,longitude,status,updated_at)
               VALUES (?,?,?,'online',datetime('now'))
               ON CONFLICT(officer_id) DO UPDATE SET
               latitude=EXCLUDED.latitude,longitude=EXCLUDED.longitude,
               status='online',updated_at=datetime('now')""",
            [u['id'], lat or 0, lng or 0])
    return jsonify({'id': sid, 'message': 'Submission saved', 'status': status})

@app.route('/api/my/submissions/<int:sid>', methods=['PUT'])
@login_required()
def api_my_update_submission(sid):
    """Update a draft to finalized or sent."""
    sub = query("SELECT * FROM form_submissions WHERE id=? AND officer_id=?",
                [sid, session['user_id']], one=True)
    if not sub:
        return jsonify({'error': 'Not found'}), 404
    d       = request.json or {}
    status  = d.get('status', sub['status'])
    data    = d.get('data', json.loads(sub['data_json'] or '{}'))
    execute("UPDATE form_submissions SET status=?,data_json=?,synced_at=datetime('now') WHERE id=?",
            [status, json.dumps(data), sid])
    # If upgrading to sent, insert into dynamic table
    if status == 'sent' and sub['status'] != 'sent':
        form = query("SELECT * FROM form_definitions WHERE id=?", [sub['form_id']], one=True)
        if form:
            schema = json.loads(form['schema_json'])
            try:
                insert_dynamic_row(form['table_name'], schema, data, sid,
                                   session['user_id'], sub['zone_id'], sub['org_id'], sub['submitted_at'])
            except Exception as e:
                print(f"Dynamic row error: {e}")
    return jsonify({'message': 'Updated', 'status': status})

@app.route('/api/my/submissions/<int:sid>', methods=['DELETE'])
@login_required()
def api_my_delete_submission(sid):
    """Only drafts can be deleted by the user."""
    sub = query("SELECT * FROM form_submissions WHERE id=? AND officer_id=?",
                [sid, session['user_id']], one=True)
    if not sub:
        return jsonify({'error': 'Not found'}), 404
    if sub['status'] != 'draft':
        return jsonify({'error': 'Only draft submissions can be deleted'}), 400
    execute("DELETE FROM form_submissions WHERE id=?", [sid])
    return jsonify({'message': 'Draft deleted'})

# ─── LOCATION PING ────────────────────────────────────────────────────────────
@app.route('/api/location/ping', methods=['POST'])
@login_required()
def api_location_ping():
    d = request.json or {}
    execute("""INSERT INTO officer_locations
               (officer_id,latitude,longitude,accuracy,battery_pct,status,updated_at)
               VALUES (?,?,?,?,?,'online',datetime('now'))
               ON CONFLICT(officer_id) DO UPDATE SET
               latitude=EXCLUDED.latitude,longitude=EXCLUDED.longitude,
               accuracy=EXCLUDED.accuracy,battery_pct=EXCLUDED.battery_pct,
               status='online',updated_at=datetime('now')""",
            [session['user_id'], d.get('latitude', 0), d.get('longitude', 0),
             d.get('accuracy'), d.get('battery_pct')])
    return jsonify({'ok': True})

# ─── ALERTS ───────────────────────────────────────────────────────────────────
@app.route('/api/alerts')
@login_required(roles=['admin', 'supervisor'])
def api_alerts():
    rows = query("""
        SELECT a.*, u.full_name as officer_name FROM alerts a
        LEFT JOIN users u ON u.id=a.officer_id
        ORDER BY a.created_at DESC LIMIT 50
    """)
    return jsonify(rows)

@app.route('/api/alerts/<int:aid>/read', methods=['PUT'])
@login_required(roles=['admin', 'supervisor'])
def api_read_alert(aid):
    execute("UPDATE alerts SET is_read=1 WHERE id=?", [aid])
    return jsonify({'ok': True})

# ─── DASHBOARD ────────────────────────────────────────────────────────────────
@app.route('/api/dashboard/summary')
@login_required(roles=['admin', 'supervisor'])
def api_dashboard_summary():
    # Auto-mark users offline if no heartbeat for 3 minutes
    try:
        execute("""UPDATE officer_locations SET status='offline'
                   WHERE status='online' AND updated_at < datetime('now','-3 minutes')""")
    except Exception as e:
        print(f"Auto-offline error: {e}")
    u = current_user()
    users = query("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN ol.status='online' THEN 1 END) as online,
               COUNT(CASE WHEN ol.status='offline' OR ol.status IS NULL THEN 1 END) as offline
        FROM users u LEFT JOIN officer_locations ol ON ol.officer_id=u.id
        WHERE u.role IN ('user','supervisor') AND u.is_active=1
    """, one=True)
    forms = query("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN status='pending_approval' THEN 1 END) as pending,
               COUNT(CASE WHEN status='approved' AND is_active=1 THEN 1 END) as active
        FROM form_definitions
    """, one=True)
    subs = query("""
        SELECT COUNT(CASE WHEN date(submitted_at)=date('now') THEN 1 END) as today,
               COUNT(CASE WHEN submitted_at>=datetime('now','-7 days') THEN 1 END) as week
        FROM form_submissions WHERE status='sent'
    """, one=True)
    orgs    = query("SELECT COUNT(*) as total FROM organizations", one=True)
    zones   = query("SELECT COUNT(*) as total FROM zones WHERE is_active=1", one=True)
    pending = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0",
                    [u['id']], one=True)
    return jsonify({
        'users': users, 'forms': forms, 'submissions': subs,
        'orgs': orgs, 'zones': zones,
        'unread_notifications': pending['c'] if pending else 0,
    })

@app.route('/api/dashboard/officer-summary')
@login_required(roles=['user'])
def api_officer_summary():
    uid   = session['user_id']
    today = query("""
        SELECT COUNT(*) as sent_today,
               COUNT(CASE WHEN status='draft' THEN 1 END) as drafts,
               COUNT(CASE WHEN status='finalized' THEN 1 END) as finalized
        FROM form_submissions WHERE officer_id=?
    """, [uid], one=True)
    recent = query("""
        SELECT fs.id, fs.status, fs.submitted_at, f.title as form_title
        FROM form_submissions fs JOIN form_definitions f ON f.id=fs.form_id
        WHERE fs.officer_id=? ORDER BY fs.submitted_at DESC LIMIT 5
    """, [uid])
    loc = query("SELECT latitude,longitude,updated_at FROM officer_locations WHERE officer_id=?", [uid], one=True)
    return jsonify({'today': today, 'recent': recent, 'location': loc})

# ─── REPORTS ─────────────────────────────────────────────────────────────────
@app.route('/api/reports/daily')
@login_required(roles=['admin', 'supervisor'])
def api_report_daily():
    date = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
    rows = query("""
        SELECT u.id, u.full_name, u.employee_id, u.org_name, u.role,
               COUNT(CASE WHEN date(fs.submitted_at)=? AND fs.status='sent' THEN 1 END) as submissions,
               ol.status as current_status, ol.updated_at as last_seen
        FROM users u
        LEFT JOIN form_submissions fs ON fs.officer_id=u.id
        LEFT JOIN officer_locations ol ON ol.officer_id=u.id
        WHERE u.role IN ('user','supervisor') AND u.is_active=1
        GROUP BY u.id, u.full_name, u.employee_id, u.org_name, u.role, ol.status, ol.updated_at
        ORDER BY submissions DESC
    """, [date])
    return jsonify({'date': date, 'officers': rows})


# ─── REPORTS (Weekly + Zones) ─────────────────────────────────────────────────
@app.route('/api/reports/weekly')
@login_required(roles=['admin', 'supervisor'])
def api_report_weekly():
    rows = query("""
        SELECT date(submitted_at) as date,
               COUNT(*) as submissions,
               COUNT(DISTINCT officer_id) as active_officers
        FROM form_submissions WHERE status='sent'
          AND submitted_at>=datetime('now','-7 days')
        GROUP BY date(submitted_at) ORDER BY date DESC
    """)
    return jsonify(rows)

@app.route('/api/reports/zones')
@login_required(roles=['admin', 'supervisor'])
def api_report_zones():
    date = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
    rows = query("""
        SELECT z.id, z.name, z.sub_county, z.ward, z.target_households, z.color,
               COUNT(DISTINCT za.officer_id) as officers_assigned,
               COUNT(CASE WHEN date(fs.submitted_at)=? THEN 1 END) as submissions_today
        FROM zones z
        LEFT JOIN zone_assignments za ON za.zone_id=z.id
        LEFT JOIN form_submissions fs ON fs.zone_id=z.id
        WHERE z.is_active=1
        GROUP BY z.id, z.name, z.sub_county, z.ward, z.target_households, z.color
        ORDER BY z.name
    """, [date])
    return jsonify(rows)

# ─── SEED ─────────────────────────────────────────────────────────────────────
@app.route('/api/seed', methods=['POST'])
def api_seed():
    count = query("SELECT COUNT(*) as c FROM users", one=True)['c']
    if count > 0:
        return jsonify({'error': 'Already seeded'}), 400
    import random

    # Organizations
    org1 = execute_returning("INSERT INTO organizations (name,slug,description) VALUES (?,?,?)",
                              ['Nairobi County Health', 'nairobi_county_health', 'Community health workers program'])
    org2 = execute_returning("INSERT INTO organizations (name,slug,description) VALUES (?,?,?)",
                              ['WaterAid Kenya', 'wateraid_kenya', 'Water and sanitation surveys'])

    # Admin
    execute_returning("INSERT INTO users (full_name,email,password,role,employee_id,org_id,org_name) VALUES (?,?,?,?,?,?,?)",
                      ['System Admin', 'admin@fieldops.demo', hash_pw('fieldops2024'), 'admin', 'ADM-001', org1, 'Nairobi County Health'])
    sup1 = execute_returning("INSERT INTO users (full_name,email,password,role,employee_id,org_id,org_name) VALUES (?,?,?,?,?,?,?)",
                              ['Dr. Sarah Kimani', 'supervisor@fieldops.demo', hash_pw('fieldops2024'), 'supervisor', 'SUP-001', org1, 'Nairobi County Health'])
    sup2 = execute_returning("INSERT INTO users (full_name,email,password,role,employee_id,org_id,org_name) VALUES (?,?,?,?,?,?,?)",
                              ['James Mwangi', 'james.sup@fieldops.demo', hash_pw('fieldops2024'), 'supervisor', 'SUP-002', org2, 'WaterAid Kenya'])

    officers = [
        ('Amina Wanjiku','amina@fieldops.demo','CHW-047', org1, 'Nairobi County Health'),
        ('Peter Kamau','peter@fieldops.demo','CHW-048', org1, 'Nairobi County Health'),
        ('Grace Auma','grace@fieldops.demo','CHW-049', org1, 'Nairobi County Health'),
        ('Samuel Njau','samuel@fieldops.demo','CHW-050', org2, 'WaterAid Kenya'),
        ('Ruth Chebet','ruth@fieldops.demo','CHW-051', org2, 'WaterAid Kenya'),
    ]
    oids = []
    for name, email, emp, oid, oname in officers:
        uid = execute_returning("INSERT INTO users (full_name,email,password,role,employee_id,org_id,org_name) VALUES (?,?,?,?,?,?,?)",
                                [name, email, hash_pw('fieldops2024'), 'user', emp, oid, oname])
        oids.append((uid, oid))

    # Zones
    zones_data = [
        ('Kibera North', 'Kibera', 'Laini Saba', 200, '#39d353', org1, -1.310, -1.328, 36.795, 36.775),
        ('Kibera East',  'Kibera', 'Soweto East', 180, '#f0a500', org1, -1.300, -1.320, 36.805, 36.790),
        ('Mathare',      'Mathare', 'Mabatini',   220, '#5a7a96', org1, -1.255, -1.275, 36.860, 36.840),
        ('Westlands',    'Westlands', 'Parklands', 150, '#00bcd4', org2, -1.255, -1.270, 36.825, 36.800),
        ('Eastleigh',    'Kamukunji', 'Eastleigh N.', 190, '#ff6b9d', org2, -1.270, -1.285, 36.855, 36.835),
    ]
    zone_ids = []
    for name, sub, ward, target, color, oid, north, south, east, west in zones_data:
        zid = execute_returning(
            "INSERT INTO zones (name,sub_county,ward,target_households,color,org_id,bbox_north,bbox_south,bbox_east,bbox_west,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            [name, sub, ward, target, color, oid, north, south, east, west])
        zone_ids.append((zid, oid))

    for uid, uorg in oids:
        matching = [zid for zid, zorg in zone_ids if zorg == uorg]
        if matching:
            execute("INSERT INTO zone_assignments (zone_id,officer_id,target_visits,assigned_by) VALUES (?,?,?,1) ON CONFLICT(zone_id,officer_id) DO NOTHING",
                    [matching[0], uid, 10])

    # Health survey form
    h_schema = [
        {'name':'household_name','label':'Household Name','type':'text','required':True,'options':[],'hint':''},
        {'name':'members_count','label':'Number of Members','type':'integer','required':True,'options':[],'hint':''},
        {'name':'has_clean_water','label':'Access to Clean Water','type':'boolean','required':True,'options':[],'hint':''},
        {'name':'vaccination_status','label':'Vaccination Status','type':'select','required':True,
         'options':['Full','Partial','None','Unknown'],'hint':''},
        {'name':'malaria_cases','label':'Malaria Cases (Last 30 Days)','type':'integer','required':False,'options':[],'hint':''},
        {'name':'notes','label':'Notes','type':'textarea','required':False,'options':[],'hint':''},
    ]
    h_table = 'form_health_survey'
    hfid = execute_returning(
        "INSERT INTO form_definitions (org_id,created_by,title,description,category,schema_json,table_name,status,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
        [org1, 1, 'Community Health Survey', 'Household health data', 'medical', json.dumps(h_schema), h_table, 'approved', 1]
    )
    create_dynamic_table(h_table, h_schema)

    # Water survey form
    w_schema = [
        {'name':'community_name','label':'Community Name','type':'text','required':True,'options':[],'hint':''},
        {'name':'water_source','label':'Water Source','type':'select','required':True,
         'options':['Borehole','River','Piped','Rainwater','Other'],'hint':''},
        {'name':'daily_litres','label':'Litres Per Day','type':'number','required':False,'options':[],'hint':''},
        {'name':'water_safe','label':'Safe for Drinking','type':'boolean','required':True,'options':[],'hint':''},
        {'name':'distance_km','label':'Distance to Source (km)','type':'number','required':False,'options':[],'hint':''},
    ]
    w_table = 'form_water_survey'
    wfid = execute_returning(
        "INSERT INTO form_definitions (org_id,created_by,title,description,category,schema_json,table_name,status,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
        [org2, sup2, 'Water Access Survey', 'Water and sanitation data', 'water_sanitation', json.dumps(w_schema), w_table, 'approved', 1]
    )
    create_dynamic_table(w_table, w_schema)

    # Pending form from supervisor
    execute_returning(
        "INSERT INTO form_definitions (org_id,created_by,title,description,category,schema_json,table_name,status,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
        [org1, sup1, 'Nutrition Survey', 'Monthly nutrition assessment', 'medical',
         json.dumps([{'name':'child_name','label':'Child Name','type':'text','required':True,'options':[],'hint':''},
                     {'name':'age_months','label':'Age (Months)','type':'integer','required':True,'options':[],'hint':''},
                     {'name':'muac_cm','label':'MUAC (cm)','type':'number','required':True,'options':[],'hint':''}]),
         'form_nutrition_survey_pending', 'pending_approval', 0]
    )
    # Notify admin of pending
    notify_admins('form_approval_request', 'Form Approval: Nutrition Survey',
                  'Dr. Sarah Kimani submitted "Nutrition Survey" for approval.')

    # Seed some submissions
    now = datetime.utcnow()
    for i, (uid, uorg) in enumerate(oids[:3]):
        fid  = hfid
        zids = [zid for zid, zo in zone_ids if zo == uorg]
        if not zids:
            continue
        zid  = zids[0]
        zone = query("SELECT * FROM zones WHERE id=?", [zid], one=True)
        for j in range(3):
            lat  = zone['bbox_south'] + random.random() * (zone['bbox_north'] - zone['bbox_south'])
            lng  = zone['bbox_west']  + random.random() * (zone['bbox_east']  - zone['bbox_west'])
            ts   = (now - timedelta(hours=j*4)).strftime('%Y-%m-%d %H:%M:%S')
            data = {'household_name': f'Family {j+1}', 'members_count': random.randint(2,8),
                    'has_clean_water': random.randint(0,1),
                    'vaccination_status': random.choice(['Full','Partial','None']),
                    'malaria_cases': random.randint(0,3), 'notes': 'Seeded'}
            sid = execute_returning(
                "INSERT INTO form_submissions (form_id,officer_id,zone_id,org_id,latitude,longitude,status,submitted_at,data_json) VALUES (?,?,?,?,?,?,?,?,?)",
                [fid, uid, zid, uorg, lat, lng, 'sent', ts, json.dumps(data)]
            )
            insert_dynamic_row(h_table, h_schema, data, sid, uid, zid, uorg, ts)

    execute_returning("INSERT INTO officer_locations (officer_id,latitude,longitude,status) VALUES (?,?,?,?) ON CONFLICT(officer_id) DO UPDATE SET latitude=EXCLUDED.latitude,longitude=EXCLUDED.longitude,status=EXCLUDED.status",
                      [oids[0][0], -1.315, 36.780, 'online'])

    return jsonify({'message': f'Seeded: 2 orgs, {len(oids)} users, 2 approved forms, 1 pending'})

# ─── INIT ─────────────────────────────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    db_label = 'PostgreSQL (Supabase)' if USE_POSTGRES else 'SQLite (local dev)'
    print(f"""
╔══════════════════════════════════════════════════════╗
║         FIELDOPS v4  — ODK-Scale Platform            ║
╠══════════════════════════════════════════════════════╣
║  Database : {db_label:<40}║
║  Port     : {port:<40}║
║  Debug    : {str(debug):<40}║
╚══════════════════════════════════════════════════════╝
""")
    app.run(debug=debug, host='0.0.0.0', port=port)
