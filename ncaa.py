"""NCAA basketball (men's and women's): rosters and headshots from ESPN, coaching staffs (with photos)
from each school's athletics site, which run on a few common platforms (SIDEARM, WMT)."""
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin

import requests

logger = logging.getLogger(__name__)

# ESPN team id: (school, nickname, color, conference, athletics site)
NCAA_TEAMS = {
    '103': ('Boston College', 'Eagles', '#8C2232', 'acc', 'bceagles.com'),
    '25': ('California', 'Golden Bears', '#041E42', 'acc', 'calbears.com'),
    '228': ('Clemson', 'Tigers', '#F56600', 'acc', 'clemsontigers.com'),
    '150': ('Duke', 'Blue Devils', '#00539B', 'acc', 'goduke.com'),
    '52': ('Florida State', 'Seminoles', '#782F40', 'acc', 'seminoles.com'),
    '59': ('Georgia Tech', 'Yellow Jackets', '#B3A369', 'acc', 'ramblinwreck.com'),
    '97': ('Louisville', 'Cardinals', '#C9001F', 'acc', 'gocards.com'),
    '2390': ('Miami', 'Hurricanes', '#F47423', 'acc', 'miamihurricanes.com'),
    '152': ('NC State', 'Wolfpack', '#CC0000', 'acc', 'gopack.com'),
    '153': ('North Carolina', 'Tar Heels', '#7BAFD4', 'acc', 'goheels.com'),
    '87': ('Notre Dame', 'Fighting Irish', '#062340', 'acc', 'fightingirish.com'),
    '221': ('Pittsburgh', 'Panthers', '#003594', 'acc', 'pittsburghpanthers.com'),
    '2567': ('SMU', 'Mustangs', '#A80000', 'acc', 'smumustangs.com'),
    '24': ('Stanford', 'Cardinal', '#8C1515', 'acc', 'gostanford.com'),
    '183': ('Syracuse', 'Orange', '#000E54', 'acc', 'cuse.com'),
    '258': ('Virginia', 'Cavaliers', '#232D4B', 'acc', 'virginiasports.com'),
    '259': ('Virginia Tech', 'Hokies', '#6A2C3E', 'acc', 'hokiesports.com'),
    '154': ('Wake Forest', 'Demon Deacons', '#CEB888', 'acc', 'godeacs.com'),
    '356': ('Illinois', 'Fighting Illini', '#FF5F05', 'big10', 'fightingillini.com'),
    '84': ('Indiana', 'Hoosiers', '#970310', 'big10', 'iuhoosiers.com'),
    '2294': ('Iowa', 'Hawkeyes', '#231F20', 'big10', 'hawkeyesports.com'),
    '120': ('Maryland', 'Terrapins', '#CE1126', 'big10', 'umterps.com'),
    '130': ('Michigan', 'Wolverines', '#00274C', 'big10', 'mgoblue.com'),
    '127': ('Michigan State', 'Spartans', '#173F35', 'big10', 'msuspartans.com'),
    '135': ('Minnesota', 'Golden Gophers', '#5E0A2F', 'big10', 'gophersports.com'),
    '158': ('Nebraska', 'Cornhuskers', '#E31937', 'big10', 'huskers.com'),
    '77': ('Northwestern', 'Wildcats', '#492F92', 'big10', 'nusports.com'),
    '194': ('Ohio State', 'Buckeyes', '#BA0C2F', 'big10', 'ohiostatebuckeyes.com'),
    '2483': ('Oregon', 'Ducks', '#00934B', 'big10', 'goducks.com'),
    '213': ('Penn State', 'Nittany Lions', '#061440', 'big10', 'gopsusports.com'),
    '2509': ('Purdue', 'Boilermakers', '#CEB888', 'big10', 'purduesports.com'),
    '164': ('Rutgers', 'Scarlet Knights', '#CE0E2D', 'big10', 'scarletknights.com'),
    '26': ('UCLA', 'Bruins', '#2774AE', 'big10', 'uclabruins.com'),
    '30': ('USC', 'Trojans', '#9D2235', 'big10', 'usctrojans.com'),
    '264': ('Washington', 'Huskies', '#33006F', 'big10', 'gohuskies.com'),
    '275': ('Wisconsin', 'Badgers', '#A00000', 'big10', 'uwbadgers.com'),
    '12': ('Arizona', 'Wildcats', '#CC0033', 'big12', 'arizonawildcats.com'),
    '9': ('Arizona State', 'Sun Devils', '#FFC627', 'big12', 'thesundevils.com'),
    '252': ('BYU', 'Cougars', '#0047BA', 'big12', 'byucougars.com'),
    '239': ('Baylor', 'Bears', '#154734', 'big12', 'baylorbears.com'),
    '2132': ('Cincinnati', 'Bearcats', '#000000', 'big12', 'gobearcats.com'),
    '38': ('Colorado', 'Buffaloes', '#CFB87C', 'big12', 'cubuffs.com'),
    '248': ('Houston', 'Cougars', '#C8102E', 'big12', 'uhcougars.com'),
    '66': ('Iowa State', 'Cyclones', '#AE192D', 'big12', 'cyclones.com'),
    '2305': ('Kansas', 'Jayhawks', '#0051BA', 'big12', 'kuathletics.com'),
    '2306': ('Kansas State', 'Wildcats', '#330A57', 'big12', 'kstatesports.com'),
    '197': ('Oklahoma State', 'Cowboys', '#FE5C00', 'big12', 'okstate.com'),
    '2628': ('TCU', 'Horned Frogs', '#4D1979', 'big12', 'gofrogs.com'),
    '2641': ('Texas Tech', 'Red Raiders', '#DA291C', 'big12', 'texastech.com'),
    '2116': ('UCF', 'Knights', '#000000', 'big12', 'ucfknights.com'),
    '254': ('Utah', 'Utes', '#BE0000', 'big12', 'utahutes.com'),
    '277': ('West Virginia', 'Mountaineers', '#EAAA00', 'big12', 'wvusports.com'),
    '13': ('Cal Poly', 'Mustangs', '#1E4D2B', 'bigw', 'gopoly.com'),
    '2934': ('Cal State Bakersfield', 'Roadrunners', '#003BAB', 'bigw', 'gorunners.com'),
    '2239': ('Cal State Fullerton', 'Titans', '#003767', 'bigw', 'fullertontitans.com'),
    '2463': ('Cal State Northridge', 'Matadors', '#B50000', 'bigw', 'gomatadors.com'),
    '2856': ('California Baptist', 'Lancers', '#000080', 'bigw', 'cbulancers.com'),
    '299': ('Long Beach State', 'Beach', '#000000', 'bigw', 'longbeachstate.com'),
    '16': ('Sacramento State', 'Hornets', '#00573C', 'bigw', 'hornetsports.com'),
    '300': ('UC Irvine', 'Anteaters', '#002B5C', 'bigw', 'ucirvinesports.com'),
    '27': ('UC Riverside', 'Highlanders', '#14234F', 'bigw', 'gohighlanders.com'),
    '28': ('UC San Diego', 'Tritons', '#000000', 'bigw', 'ucsdtritons.com'),
    '2540': ('UC Santa Barbara', 'Gauchos', '#1E1840', 'bigw', 'ucsbgauchos.com'),
    '3084': ('Utah Valley', 'Wolverines', '#004812', 'bigw', 'gouvu.com'),
    # CAA
    '2097': ('Campbell', 'Fighting Camels', '#000000', 'caa', 'gocamels.com'),
    '232': ('Charleston', 'Cougars', '#7A2531', 'caa', 'cofcsports.com'),
    '2182': ('Drexel', 'Dragons', '#020260', 'caa', 'drexeldragons.com'),
    '2210': ('Elon', 'Phoenix', '#020303', 'caa', 'elonphoenix.com'),
    '2261': ('Hampton', 'Pirates', '#0067AC', 'caa', 'hamptonpirates.com'),
    '2275': ('Hofstra', 'Pride', '#003594', 'caa', 'gohofstra.com'),
    '2405': ('Monmouth', 'Hawks', '#051844', 'caa', 'monmouthhawks.com'),
    '2448': ('North Carolina A&T', 'Aggies', '#0505AA', 'caa', 'ncataggies.com'),
    '111': ('Northeastern', 'Huskies', '#CC0001', 'caa', 'gonu.com'),
    '2619': ('Stony Brook', 'Seawolves', '#990000', 'caa', 'stonybrookathletics.com'),
    '119': ('Towson', 'Tigers', '#FFC229', 'caa', 'towsontigers.com'),
    '350': ('UNC Wilmington', 'Seahawks', '#00665E', 'caa', 'uncwsports.com'),
    '2729': ('William & Mary', 'Tribe', '#115740', 'caa', 'tribeathletics.com'),
    '333': ('Alabama', 'Crimson Tide', '#9E1B32', 'sec', 'rolltide.com'),
    '8': ('Arkansas', 'Razorbacks', '#A32136', 'sec', 'arkansasrazorbacks.com'),
    '2': ('Auburn', 'Tigers', '#002B5C', 'sec', 'auburntigers.com'),
    '57': ('Florida', 'Gators', '#0021A5', 'sec', 'floridagators.com'),
    '61': ('Georgia', 'Bulldogs', '#BA0C2F', 'sec', 'georgiadogs.com'),
    '96': ('Kentucky', 'Wildcats', '#0033A0', 'sec', 'ukathletics.com'),
    '99': ('LSU', 'Tigers', '#461D76', 'sec', 'lsusports.net'),
    '344': ('Mississippi State', 'Bulldogs', '#5D1725', 'sec', 'hailstate.com'),
    '142': ('Missouri', 'Tigers', '#F1B82D', 'sec', 'mutigers.com'),
    '201': ('Oklahoma', 'Sooners', '#990000', 'sec', 'soonersports.com'),
    '145': ('Ole Miss', 'Rebels', '#13294B', 'sec', 'olemisssports.com'),
    '2579': ('South Carolina', 'Gamecocks', '#73000A', 'sec', 'gamecocksonline.com'),
    '2633': ('Tennessee', 'Volunteers', '#FF8200', 'sec', 'utsports.com'),
    '251': ('Texas', 'Longhorns', '#AF5C37', 'sec', 'texassports.com'),
    '245': ('Texas A&M', 'Aggies', '#500000', 'sec', '12thman.com'),
    '238': ('Vanderbilt', 'Commodores', '#000000', 'sec', 'vucommodores.com'),
}

CONFERENCES = {
    'acc': ('ACC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/acc.png'),
    'big10': ('Big Ten', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_ten.png'),
    'big12': ('Big 12', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_12.png'),
    'sec': ('SEC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/sec.png'),
    'bigw': ('Big West', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_west.png'),
    'caa': ('CAA', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/colonial.png'),
}
NCAA_LOGO = '/static/img/ncaa.svg'   # referee spot for games between conferences

GENDERS = {'mens': "Men's", 'womens': "Women's"}

# Schools whose basketball pages aren't at /sports/<mens|womens>-basketball/: (men's page, women's page)
STAFF_PAGES = {
    'arkansasrazorbacks.com': ('/sport/m-baskbl/roster/', '/sport/w-baskbl/roster/'),
    'ramblinwreck.com': ('/sports/m-baskbl/roster/', '/sports/w-baskbl/roster/'),
    'hawkeyesports.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'ukathletics.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'fightingirish.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'gamecocksonline.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'vucommodores.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'clemsontigers.com': (None, '/sports/w-basketball/roster'),
    'virginiasports.com': (None, '/sports/wbball/roster'),
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/130.0 Safari/537.36'}
ROSTER_SECONDS = 10 * 60
STAFF_SECONDS = 6 * 3600

_cache = {}


def _cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def team_key(gender, team_id):
    """One id per team per gender, so each sheet remembers its own order and coaches"""
    return f"{gender[0]}{team_id}"


def logo_url(team_id):
    return f'https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png'


def teams():
    """[{id, school, name, color, conf, conf_name, logo}] grouped by conference, then school."""
    order = list(CONFERENCES)
    return sorted(({'id': i, 'school': s, 'name': n, 'color': c, 'conf': conf, 'conf_name': CONFERENCES[conf][0], 'logo': logo_url(i)}
                   for i, (s, n, c, conf, _) in NCAA_TEAMS.items()), key=lambda t: (order.index(t['conf']), t['school']))


# ---------- Coaching staffs from school athletics sites ----------
def _nuxt(s):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m: return None
    try: return json.loads(m.group(1))
    except Exception: return None

def _from_sidearm_data(arr, base):
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and {'firstName', 'lastName', 'title', 'image'} <= o.keys():
            first, last, title, img = v(o['firstName']), v(o['lastName']), v(o['title']), v(o['image'])
            if not isinstance(title, str) or not isinstance(first, str): continue
            url = ''
            if isinstance(img, dict):
                url = v(img.get('absoluteUrl')) or v(img.get('url')) or ''
                if url: url = urljoin(base, url)
            out.append((f'{first} {last or ""}'.strip(), title, url))
    return out

def _from_wmt_data(arr, base):
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and {'first_name', 'last_name', 'position'} <= o.keys() and ('master_photo' in o or 'photo' in o):
            first, last, pos = v(o['first_name']), v(o['last_name']), v(o['position'])
            if not isinstance(pos, str) or not isinstance(first, str): continue
            photo = v(o.get('master_photo')) or v(o.get('photo'))
            url = v(photo.get('url')) if isinstance(photo, dict) else ''
            out.append((f'{first} {last or ""}'.strip(), pos, url or ''))
    return out

def _from_sidearm_cards(s, base):
    out = []
    for chunk in s.split('s-person-card ')[1:]:
        n = re.search(r'<h3[^>]*>(.*?)</h3>', chunk, re.S); p = re.search(r's-person-details__position[^>]*>\s*<div>(.*?)</div>', chunk, re.S)
        if not n or not p: continue
        img = re.search(r'crop\?url=([^&"]+)', chunk)
        out.append((html.unescape(re.sub('<.*?>', '', n.group(1))).strip(), html.unescape(p.group(1)).strip(), unquote(img.group(1)) if img else ''))
    return out

def _from_sidearm_classic(s, base):
    out = []
    for chunk in s.split('sidearm-roster-coach ')[1:]:
        n = re.search(r'sidearm-roster-coach-name[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)', chunk)
        p = re.search(r'sidearm-roster-coach-title[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)', chunk)
        img = re.search(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', chunk, re.I)
        if n and p:
            out.append((html.unescape(n.group(1)).strip(), html.unescape(p.group(1)).strip(), urljoin(base, img.group(1).split('?')[0]) if img else ''))
    return out

def _from_coach_links(s, base):
    """Older WMT/WordPress sites (Kentucky, South Carolina): each coach's photo and name link to /roster/coach/<name>/"""
    photos, out = {}, []
    for chunk in re.split(r'(?=<a\s+href="[^"]*/roster/coach/)', s)[1:]:
        anchor = chunk[:chunk.find('</a>') + 4] if '</a>' in chunk else chunk[:2000]
        name = re.search(r'(?:alt|title)="([^"]+)"', anchor) or re.search(r'>\s*([^<>]{3,60}?)\s*</a>$', anchor)
        if not name: continue
        name = html.unescape(name.group(1)).strip()
        img = re.search(r'(?:data-src|data-bg|src)="(https?://[^"]+/imgproxy/[^"]+)"', anchor)
        if img: photos.setdefault(name, img.group(1))
        pos = re.search(r'class="(?:roster-item__info|position)"[^>]*>\s*([^<]+)', chunk[:3000])
        if pos: out.append([name, html.unescape(pos.group(1)).strip(), ''])
    return [(n, p, photos.get(n, '')) for n, p, _ in out]

def _from_staff_table(s, base):
    """Arkansas: a staff table of names and titles; each coach's photo is on their own page"""
    rows = re.findall(r'<a href="([^"]*/coache/[^"]*)">\s*([^<]+?)\s*</a>\s*</td>\s*<td>\s*([^<]+?)\s*</td>', s)
    def photo(page, name):
        try:
            p = requests.get(page, headers=HEADERS, timeout=10).text
            m = re.search(r'<img[^>]+src="([^"]+)"[^>]*alt="[^"]*' + re.escape(name.split()[-1]), p)
            return m.group(1) if m else ''
        except Exception:
            return ''
    coaches = [(page, html.unescape(name), html.unescape(title)) for page, name, title in rows if 'coach' in title.lower()]
    with ThreadPoolExecutor(5) as pool:
        photos = list(pool.map(lambda r: photo(r[0], r[1]), coaches))
    return [(name, title, url) for (_, name, title), url in zip(coaches, photos)]


def _staff_from_site(domain, gender):
    custom = STAFF_PAGES.get(domain, (None, None))[0 if gender == 'mens' else 1]
    paths = [custom] if custom else [f'/sports/{gender}-basketball/coaches', f'/sports/{gender}-basketball/roster']
    not_bench = ('strength', 's&c', 'conditioning', 'performance', 'trainer', 'video')
    keep = lambda cs: [c for c in cs if 'coach' in c[1].lower() and not any(w in c[1].lower() for w in not_bench)]
    for path in paths:
        base = f'https://{domain}{path}'
        try:
            res = requests.get(base, headers=HEADERS, timeout=12)
        except Exception:
            continue
        if res.status_code != 200:
            continue
        arr = _nuxt(res.text)
        parsers = ([lambda: _from_sidearm_data(arr, base), lambda: _from_wmt_data(arr, base)] if arr else []) + [
            lambda: _from_sidearm_cards(res.text, base), lambda: _from_sidearm_classic(res.text, base),
            lambda: _from_coach_links(res.text, base), lambda: _from_staff_table(res.text, base)]
        for parse in parsers:
            found = keep(parse())
            if len(found) >= 2:
                seen, out = set(), []
                for name, title, photo in found:
                    name = re.sub(r'\s+', ' ', name).strip()
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        out.append({'name': name, 'role': re.sub(r'\s+', ' ', title).strip(), 'headshot': photo or None})
                return out
    return []


def _rank(role):
    r = role.lower()
    if 'associate head' in r or 'assistant head' in r:
        return 1
    if 'head' in r and 'assistant' not in r and 'associate' not in r:
        return 0
    if 'associate' in r:
        return 1
    if r.startswith('assistant coach'):
        return 2
    return 3


def _coaches(team_id, gender, espn_head):
    domain = NCAA_TEAMS[team_id][4]
    def load():
        try:
            staff = _staff_from_site(domain, gender)
        except Exception as e:
            logger.warning(f"NCAA STAFF FAIL | {domain} {gender} | {e}")
            staff = []
        if not staff:
            logger.info(f"NCAA STAFF | {domain} {gender} | none found, using ESPN's head coach")
        return staff
    staff = list(_cached(f'staff:{domain}:{gender}', STAFF_SECONDS, load))
    if espn_head and not any(_rank(c['role']) == 0 for c in staff):
        staff.insert(0, {'name': espn_head, 'role': 'Head Coach', 'headshot': None})
    staff.sort(key=lambda c: _rank(c['role']))   # stable: keeps the school's order within each group
    for c in staff:
        c['id'] = re.sub(r'[^a-z0-9]+', '-', c['name'].lower()).strip('-')
    return staff


# ---------- Rosters from ESPN ----------
def _roster(gender, team_id):
    def load():
        res = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{gender}-college-basketball/teams/{team_id}/roster',
                           headers=HEADERS, timeout=15)
        res.raise_for_status()
        return res.json()
    return _cached(f'roster:{gender}:{team_id}', ROSTER_SECONDS, load)


def team_data(gender, team_id):
    """Everything the sheet needs for one team: players (jersey order) and coaching staff."""
    school, name, color, conf, _ = NCAA_TEAMS[team_id]
    data = _roster(gender, team_id)
    players = []
    for a in data.get('athletes') or []:
        players.append({'id': str(a['id']), 'first': a.get('firstName') or '', 'last': a.get('lastName') or a.get('displayName') or '',
                        'number': str(a.get('jersey') or '').strip(), 'pos': (a.get('position') or {}).get('abbreviation') or '',
                        'headshot': (a.get('headshot') or {}).get('href')})
    players.sort(key=lambda p: (not p['number'].isdigit(), int(p['number']) if p['number'].isdigit() else 0, p['last']))
    head = data.get('coach') or []
    espn_head = f"{head[0].get('firstName', '')} {head[0].get('lastName', '')}".strip() if head else ''
    return {'abbr': team_key(gender, team_id), 'city': school, 'name': name, 'color': color, 'conf': conf,
            'logo': logo_url(team_id), 'players': players, 'coaches': _coaches(team_id, gender, espn_head)}


def matchup(gender, away, home):
    with ThreadPoolExecutor(2) as pool:
        teams = list(pool.map(lambda t: team_data(gender, t), [away, home]))
    conf = teams[0]['conf'] if teams[0]['conf'] == teams[1]['conf'] else None
    return {'teams': teams, 'gender': gender,
            'ref_logo': CONFERENCES[conf][1] if conf else NCAA_LOGO,
            'ref_logo_alt': CONFERENCES[conf][0] if conf else 'NCAA'}
