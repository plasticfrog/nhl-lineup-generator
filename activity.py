"""Site activity log: every lineup/sheet generated, photo uploaded and sheet printed, stored in
SQLite next to the coach photos (the Railway volume) and shown on the /activity dashboard."""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid

import requests
from flask import request

DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, 'activity.db')
VISITOR_COOKIE = 'vid'


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _db() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,       -- nhl_screenshot, nhl_numbers, mlb, coach_photo, print_nhl, print_mlb
            summary TEXT NOT NULL,    -- human-readable: "SJS", "Tigers @ Pirates", ...
            detail TEXT,              -- "18/18 players matched", error text, ...
            ok INTEGER NOT NULL,
            secs REAL,
            visitor TEXT,
            ip TEXT,
            ua TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS events_ts ON events(ts)')
        db.execute('CREATE TABLE IF NOT EXISTS visitor_names (visitor TEXT PRIMARY KEY, name TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS ip_places (ip TEXT PRIMARY KEY, place TEXT, network TEXT, looked_up REAL)')


def client_ip():
    # Railway appends its own edge address: "client, edge"
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else request.remote_addr


def visitor_id():
    """Stable per-browser id from a cookie (set in ensure_visitor_cookie); falls back to ip+browser."""
    vid = request.cookies.get(VISITOR_COOKIE)
    if vid and re.fullmatch(r'[a-f0-9]{16}', vid):
        return vid
    raw = f"{client_ip()}|{request.headers.get('User-Agent', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def ensure_visitor_cookie(response):
    if not request.cookies.get(VISITOR_COOKIE) and response.mimetype == 'text/html':
        response.set_cookie(VISITOR_COOKIE, uuid.uuid4().hex[:16], max_age=5 * 365 * 24 * 3600,
                            samesite='Lax', secure=request.is_secure, httponly=True)
    return response


def record(kind, summary, ok=True, secs=None, detail=''):
    try:
        with _db() as db:
            db.execute('INSERT INTO events (ts, kind, summary, detail, ok, secs, visitor, ip, ua) VALUES (?,?,?,?,?,?,?,?,?)',
                       (time.time(), kind, summary, detail, 1 if ok else 0, secs, visitor_id(), client_ip(),
                        request.headers.get('User-Agent', '')[:400]))
    except Exception:
        pass  # activity logging must never break the site


def describe_browser(ua):
    ua = ua or ''
    if 'Edg/' in ua: browser = 'Edge'
    elif 'OPR/' in ua or 'Opera' in ua: browser = 'Opera'
    elif 'Firefox/' in ua: browser = 'Firefox'
    elif 'Chrome/' in ua or 'CriOS' in ua: browser = 'Chrome'
    elif 'Safari/' in ua: browser = 'Safari'
    elif 'python-requests' in ua: browser = 'Script'
    else: browser = 'Other'
    if 'iPhone' in ua: device = 'iPhone'
    elif 'iPad' in ua: device = 'iPad'
    elif 'Android' in ua: device = 'Android'
    elif 'Macintosh' in ua: device = 'Mac'
    elif 'Windows' in ua: device = 'Windows'
    elif 'Linux' in ua: device = 'Linux'
    else: device = ''
    return f'{browser} on {device}' if device else browser


def _places(ips):
    """City/region and network (ISP or company) per IP, looked up once and cached."""
    ips = [ip for ip in set(ips) if ip]
    with _db() as db:
        known = {r['ip']: dict(r) for r in db.execute(
            f"SELECT * FROM ip_places WHERE ip IN ({','.join('?' * len(ips))})", ips)} if ips else {}
    missing = [ip for ip in ips if ip not in known and not ip.startswith(('127.', '10.', '192.168.', '::1'))]
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        try:
            res = requests.post('http://ip-api.com/batch?fields=status,query,city,regionName,countryCode,isp,org',
                                json=chunk, timeout=6).json()
        except Exception:
            break
        with _db() as db:
            for r in res:
                if r.get('status') != 'success':
                    continue
                place = ', '.join(p for p in [r.get('city'), r.get('regionName'),
                                              r.get('countryCode') if r.get('countryCode') != 'US' else ''] if p)
                network = r.get('org') or r.get('isp') or ''
                db.execute('INSERT OR REPLACE INTO ip_places VALUES (?,?,?,?)', (r['query'], place, network, time.time()))
                known[r['query']] = {'place': place, 'network': network}
    return known


def dashboard_data(days=30, limit=1000):
    since = time.time() - days * 86400
    with _db() as db:
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT ?', (since, limit))]
        names = {r['visitor']: r['name'] for r in db.execute('SELECT * FROM visitor_names')}
    places = _places([r['ip'] for r in rows])
    for r in rows:
        p = places.get(r['ip'], {})
        r['browser'] = describe_browser(r.pop('ua'))
        r['place'] = p.get('place', '')
        r['network'] = p.get('network', '')
        r['name'] = names.get(r['visitor'], '')
    return rows


def set_visitor_name(visitor, name):
    with _db() as db:
        if name:
            db.execute('INSERT OR REPLACE INTO visitor_names VALUES (?,?)', (visitor, name[:40]))
        else:
            db.execute('DELETE FROM visitor_names WHERE visitor = ?', (visitor,))


def as_json(rows):
    return json.dumps(rows)
