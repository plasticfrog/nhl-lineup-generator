"""NBA teams, rosters and coaching staffs from NBA.com team pages; headshots from NBA's CDN."""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# abbr: (team id, city, name, color)
NBA_TEAMS = {
    'ATL': (1610612737, 'Atlanta', 'Hawks', '#C8102E'),
    'BOS': (1610612738, 'Boston', 'Celtics', '#007A33'),
    'BKN': (1610612751, 'Brooklyn', 'Nets', '#000000'),
    'CHA': (1610612766, 'Charlotte', 'Hornets', '#1D1160'),
    'CHI': (1610612741, 'Chicago', 'Bulls', '#CE1141'),
    'CLE': (1610612739, 'Cleveland', 'Cavaliers', '#860038'),
    'DAL': (1610612742, 'Dallas', 'Mavericks', '#00538C'),
    'DEN': (1610612743, 'Denver', 'Nuggets', '#0E2240'),
    'DET': (1610612765, 'Detroit', 'Pistons', '#C8102E'),
    'GSW': (1610612744, 'Golden State', 'Warriors', '#1D428A'),
    'HOU': (1610612745, 'Houston', 'Rockets', '#CE1141'),
    'IND': (1610612754, 'Indiana', 'Pacers', '#002D62'),
    'LAC': (1610612746, 'LA', 'Clippers', '#C8102E'),
    'LAL': (1610612747, 'Los Angeles', 'Lakers', '#552583'),
    'MEM': (1610612763, 'Memphis', 'Grizzlies', '#5D76A9'),
    'MIA': (1610612748, 'Miami', 'Heat', '#98002E'),
    'MIL': (1610612749, 'Milwaukee', 'Bucks', '#00471B'),
    'MIN': (1610612750, 'Minnesota', 'Timberwolves', '#0C2340'),
    'NOP': (1610612740, 'New Orleans', 'Pelicans', '#0C2340'),
    'NYK': (1610612752, 'New York', 'Knicks', '#006BB6'),
    'OKC': (1610612760, 'Oklahoma City', 'Thunder', '#007AC1'),
    'ORL': (1610612753, 'Orlando', 'Magic', '#0077C0'),
    'PHI': (1610612755, 'Philadelphia', '76ers', '#006BB6'),
    'PHX': (1610612756, 'Phoenix', 'Suns', '#1D1160'),
    'POR': (1610612757, 'Portland', 'Trail Blazers', '#E03A3E'),
    'SAC': (1610612758, 'Sacramento', 'Kings', '#5A2D81'),
    'SAS': (1610612759, 'San Antonio', 'Spurs', '#000000'),
    'TOR': (1610612761, 'Toronto', 'Raptors', '#CE1141'),
    'UTA': (1610612762, 'Utah', 'Jazz', '#002B5C'),
    'WAS': (1610612764, 'Washington', 'Wizards', '#002B5C'),
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/130.0 Safari/537.36'}
CACHE_SECONDS = 10 * 60
# NBA serves a small grey silhouette at the headshot address of anyone it has no photo for
PLACEHOLDER_MAX_BYTES = 20000

_cache = {}


def _cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def logo_url(abbr):
    return f'https://cdn.nba.com/logos/nba/{NBA_TEAMS[abbr][0]}/primary/L/logo.svg'


def headshot_url(person_id):
    return f'https://cdn.nba.com/headshots/nba/latest/1040x760/{person_id}.png'


def teams():
    """[{abbr, city, name, color, logo}] sorted by full name."""
    return sorted(({'abbr': a, 'city': c, 'name': n, 'color': col, 'logo': logo_url(a)}
                   for a, (_, c, n, col) in NBA_TEAMS.items()), key=lambda t: f"{t['city']} {t['name']}")


def _team_page(abbr):
    def load():
        res = requests.get(f'https://www.nba.com/team/{NBA_TEAMS[abbr][0]}', headers=HEADERS, timeout=15)
        res.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', res.text, re.S)
        if not m:
            raise ValueError('NBA.com team page changed')
        return json.loads(m.group(1))['props']['pageProps']['team']
    return _cached(f'page:{abbr}', CACHE_SECONDS, load)


def _has_photo(url):
    def check():
        try:
            res = requests.head(url, headers=HEADERS, timeout=8)
            return res.ok and int(res.headers.get('content-length') or 0) > PLACEHOLDER_MAX_BYTES
        except Exception:
            return True  # can't check: keep the photo
    return _cached(f'photo:{url}', 6 * 3600, check)


def _photos(ids):
    urls = [headshot_url(i) for i in ids]
    with ThreadPoolExecutor(16) as pool:
        return dict(zip(ids, (u if ok else None for u, ok in zip(urls, pool.map(_has_photo, urls)))))


def _split_name(full):
    parts = full.split(' ', 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ('', full)


def team_data(abbr):
    """Everything the sheet needs for one team: players (jersey order) and coaching staff."""
    _, city, name, color = NBA_TEAMS[abbr]
    page = _team_page(abbr)

    players = []
    for p in page.get('roster') or []:
        first = p.get('NICKNAME') or ''
        full = p.get('PLAYER') or ''
        last = full[len(first):].strip() if first and full.startswith(first) else _split_name(full)[1]
        players.append({'id': str(p['PLAYER_ID']), 'first': first, 'last': last,
                        'number': str(p.get('NUM') or '').strip(), 'pos': p.get('POSITION') or ''})
    players.sort(key=lambda p: (not p['number'].isdigit(), int(p['number']) if p['number'].isdigit() else 0, p['last']))

    staff = []
    for c in page.get('coaches') or []:
        role = (c.get('COACH_TYPE') or '').strip()
        if 'coach' not in role.lower():
            continue  # trainers, etc.
        staff.append({'id': str(c['COACH_ID']), 'name': (c.get('COACH_NAME') or '').strip(), 'role': role})
    def rank(c):
        role = c['role'].lower()
        return (0 if role == 'head coach' else 1 if 'associate' in role else 2 if role == 'assistant coach'
                else 3 if 'assistant' in role else 4)
    staff.sort(key=rank)  # stable: keeps NBA.com's order within each group

    photos = _photos([p['id'] for p in players] + [c['id'] for c in staff])
    for x in players + staff:
        x['headshot'] = photos.get(x['id'])

    return {'abbr': abbr, 'city': city, 'name': name, 'color': color, 'logo': logo_url(abbr),
            'players': players, 'coaches': staff}
