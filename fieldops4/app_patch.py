# Read app.py and patch it
with open('/home/claude/fieldops4/app.py', 'r') as f:
    content = f.read()

# 1. Add load_dotenv at the top
if 'load_dotenv' not in content:
    content = content.replace(
        'import os, hashlib, hmac, secrets, json, csv, io, re',
        'import os, hashlib, hmac, secrets, json, csv, io, re\nfrom dotenv import load_dotenv\nload_dotenv()'
    )
    print('✓ Added load_dotenv')

# 2. Add missing report endpoints before the seed route
reports_code = '''
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
        WHERE z.is_active=1 GROUP BY z.id ORDER BY z.name
    """, [date])
    return jsonify(rows)

'''

if 'api_report_weekly' not in content:
    content = content.replace('# ─── SEED ──', reports_code + '# ─── SEED ──')
    print('✓ Added weekly + zones report endpoints')

with open('/home/claude/fieldops4/app.py', 'w') as f:
    f.write(content)
print('app.py patched successfully')
