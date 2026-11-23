"""College football headshot sheets: rosters and headshots from ESPN, coaching staffs from the school sites.
"""
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

import ncaa

logger = logging.getLogger(__name__)

# ESPN's FBS and FCS conferences (2026 season): key -> (name, logo, [ESPN team ids])
_CONF = 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/'
CONFERENCES = {
    'sec': ('SEC', _CONF + 'sec.png', '2 8 57 61 96 99 142 145 201 238 245 251 333 344 2579 2633'),
    'big10': ('Big Ten', _CONF + 'big_ten.png', '26 30 77 84 120 127 130 135 158 164 194 213 264 275 356 2294 2483 2509'),
    'acc': ('ACC', _CONF + 'acc.png', '24 25 52 59 97 103 150 152 153 154 183 221 228 258 259 2390 2567'),
    'big12': ('Big 12', _CONF + 'big_12.png', '9 12 38 66 197 239 248 252 254 277 2116 2132 2305 2306 2628 2641'),
    'fbsind': ('FBS Independents', _CONF + 'fbs_independents.png', '41 87'),
    'pac12': ('Pac-12', _CONF + 'pac_12.png', '21 36 68 204 265 278 326 328'),
    'aac': ('American', _CONF + 'american.png', '5 58 151 202 218 235 242 249 349 2226 2426 2429 2636 2655'),
    'mwc': ('Mountain West', _CONF + 'mountain_west.png', '23 62 167 2005 2439 2440 2449 2459 2638 2751'),
    'sunbelt': ('Sun Belt', _CONF + 'sun_belt.png', '6 256 276 290 295 309 324 2026 2032 2247 2348 2433 2572 2653'),
    'mac': ('MAC', _CONF + 'mid_american.png', '16 113 189 193 195 2006 2050 2084 2117 2199 2309 2649 2711'),
    'cusa': ('Conference USA', _CONF + 'conference_usa.png', '48 55 98 166 338 2229 2335 2393 2534 2623'),
    'bigsky': ('Big Sky', _CONF + 'big_sky.png', '13 70 147 149 253 302 304 331 2458 2464 2502 2692 3101'),
    'caa': ('CAA', _CONF + 'caa.png', '119 160 227 311 399 2097 2210 2261 2405 2448 2529 2619 2803'),
    'mvfc': ('Missouri Valley', _CONF + 'missouri_valley.png', '79 93 155 233 282 2287 2460 2571 2754'),
    'ivy': ('Ivy League', _CONF + 'ivy.png', '43 108 159 163 171 172 219 225'),
    'patriot': ('Patriot League', _CONF + 'patriot_league.png', '46 107 222 257 322 2083 2142 2230 2329 2729'),
    'socon': ('SoCon', _CONF + 'southern.png', '231 236 2193 2382 2535 2635 2643 2678 2717 2747'),
    'southland': ('Southland', _CONF + 'southland.png', '292 2277 2320 2377 2447 2466 2545 2617 2837 2916'),
    'uac': ('UAC', _CONF + 'uac.png', '2000 2046 2110 2198 2453 2627 2698 110242'),
    'ovc': ('Big South-OVC', _CONF + 'ovc.png', '2127 2197 2241 2546 2630 2634 2710 2815'),
    'nec': ('NEC', _CONF + 'northeast.png', '284 2115 2184 2341 2385 2441 2523 2681'),
    'pioneer': ('Pioneer League', _CONF + 'pioneer.png', '56 301 2086 2166 2168 2181 2368 2413 2506 2674 2900'),
    'swac': ('SWAC', _CONF + 'swac.png', '50 2010 2011 2016 2029 2065 2296 2400 2504 2582 2640 2755'),
    'meac': ('MEAC', _CONF + 'meac.png', '47 2169 2415 2428 2450 2569'),
    'fcsind': ('FCS Independents', _CONF + 'fcs_independents.png', '2130 2771'),
}
# Football teams the basketball list doesn't have (or has under another ESPN id): id -> (school, name, color, domain)
EXTRA = {
    '2341': ('LIU', 'Sharks', '#50C9F7', 'liuathletics.com'),
    '110242': ('West Florida', 'Argonauts', '#004C97', 'goargos.com'),
}
# Schools whose football pages aren't at /sports/football/: path of the coaches or roster page
STAFF_PAGES = {
    'arkansasrazorbacks.com': '/sport/m-footbl/roster/',
    'ramblinwreck.com': '/sports/m-footbl/roster/',
}

TEAMS = {}   # id -> (school, name, color, conf, domain)
for _conf, (_n, _l, _ids) in CONFERENCES.items():
    for _id in _ids.split():
        _school, _name, _color, _, _domain = ncaa.NCAA_TEAMS[_id] if _id in ncaa.NCAA_TEAMS else (*EXTRA[_id][:3], None, EXTRA[_id][3])
        TEAMS[_id] = (_school, _name, _color, _conf, _domain)

ROSTER_SECONDS = 30 * 60
STAFF_SECONDS = 6 * 3600


def team_key(team_id):
    return f'f{team_id}'


def teams():
    order = list(CONFERENCES)
    return sorted(({'id': i, 'school': s, 'name': n, 'color': c, 'conf': conf, 'conf_name': CONFERENCES[conf][0], 'logo': ncaa.logo_url(i)}
                   for i, (s, n, c, conf, _) in TEAMS.items()), key=lambda t: (order.index(t['conf']), t['school']))


def conferences():
    return {k: (n, logo) for k, (n, logo, _) in CONFERENCES.items()}


# ---------- Roster from ESPN ----------
SIDES = {'offense': 'OFF', 'defense': 'DEF', 'specialTeam': 'ST'}


def _roster(team_id):
    res = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{team_id}/roster?limit=300',
                       headers=ncaa.HEADERS, timeout=15)
    res.raise_for_status()
    return res.json()


def _players(data):
    out, seen = [], set()
    for group in data.get('athletes') or []:
        side = SIDES.get(group.get('position'), '')
        for a in group.get('items') or []:
            if a['id'] in seen:
                continue
            seen.add(a['id'])
            out.append({'id': str(a['id']), 'first': a.get('firstName') or '', 'last': a.get('lastName') or a.get('displayName') or '',
                        'number': str(a.get('jersey') or '').strip(), 'pos': (a.get('position') or {}).get('abbreviation') or '',
                        'side': side, 'year': (a.get('experience') or {}).get('abbreviation') or '',
                        'headshot': (a.get('headshot') or {}).get('href')})
    return out


_key = lambda first, last: re.sub(r'[^a-z]', '', f'{first}{last}'.lower())
_last = lambda last: re.sub(r'[^a-z]', '', re.sub(r'\s+(jr|sr|ii|iii|iv)\.?$', '', last.lower()))


def _merge(players, site):
    """ESPN's list plus anyone only the school lists (walk-ons); school photos fill in where ESPN has none"""
    by_name = {_key(p['first'], p['last']): p for p in players}
    by_num = {}
    for p in players:
        by_num.setdefault((p['number'], _last(p['last'])), p)
    for sp in site:
        p = by_name.get(_key(sp['first'], sp['last'])) or by_num.get((sp['number'], _last(sp['last'])))
        if p:
            if not p['headshot'] and sp['headshot']:
                p['headshot'], p['fill'] = sp['headshot'], True   # a school portrait: fill the box, not the cutout framing
            if not p['number'] and sp['number']:
                p['number'] = sp['number']
        elif sp['first'] or sp['last']:
            players.append({'id': 's-' + sp['id'], 'first': sp['first'], 'last': sp['last'], 'number': sp['number'], 'pos': sp['pos'],
                            'side': '', 'year': '', 'headshot': sp['headshot'], 'fill': bool(sp['headshot'])})
    return players


def _site_roster(domain):
    path = STAFF_PAGES.get(domain, '/sports/football/roster')
    path = re.sub(r'/coaches(/index)?/?$', '/roster', path)
    try:
        return ncaa.site_players(domain, [path], least=30, deep=False)
    except Exception as e:
        logger.warning(f"FOOTBALL ROSTER FAIL | {domain} | {e}")
        return []


# ---------- Coaching staff from the school site ----------
# Not on-field coaches, or not coaches at all
_SKIP = ('strength', 's&c', 'conditioning', 'performance', 'trainer', 'video', 'academic', 'analyst', 'graduate', 'quality control',
         'intern', 'recruiting', 'personnel', 'operations', 'executive assistant', 'administrative', 'assistant to', 'nutrition',
         'equipment', 'chief of staff', 'director of', 'football relations', 'player development', 'creative', 'scouting')


# Oregon lists position coaches by their group alone: "Tight Ends"
_GROUPS = re.compile(r'quarterback|running back|wide receiver|receivers|tight end|offensive line|defensive line|linebacker|cornerback|'
                     r'safeties|secondary|defensive back|special teams|defensive end|edge|nickel|outside backers|inside backers', re.I)


def _is_coach(title):
    t = title.lower()
    return ('coach' in t or 'coordinator' in t or _GROUPS.search(t)) and not any(w in t for w in _SKIP)


def role_slot(title):
    """0 head coach, 1 offensive coordinator, 2 defensive coordinator, 3 special teams, 4 everyone else"""
    t = title.lower()
    if re.search(r'\bhead (football )?coach\b', t) and not re.search(r'(associate|assistant|asst\.?) head', t):
        return 0
    if re.search(r'\boff(ensive|\.)? ?coor|\bco-?oc\b', t):
        return 1
    if re.search(r'\bdef(ensive|\.)? ?coor|\bco-?dc\b', t):
        return 2
    if 'special teams' in t and 'assistant special' not in t:
        return 3
    return 4


def _from_coach_rows(s, base):
    """SIDEARM staff tables (Utah State): <tr class="sidearm-coaches-coach"> with the name link, then the title"""
    return [(html.unescape(n), html.unescape(t), '') for n, t in
            re.findall(r'sidearm-coaches-coach".*?<a[^>]*>\s*([^<]+?)\s*</a>\s*</th>\s*<td[^>]*>\s*([^<]+?)\s*</td>', s, re.S)]


def _from_bio_blocks(s, base):
    """Hand-made coach pages (Illinois): photo, then "<strong>Name | </strong> Bio<br> Title<br>"""
    return [(html.unescape(n), html.unescape(t), urljoin(base, img)) for img, n, t in
            re.findall(r'<img[^>]*src="([^"]+)"[^>]*>\s*</p>\s*<p>\s*<strong>\s*([^<|]+?)\s*\|\s*</strong>.*?<br\s*/?>\s*([^<]+?)\s*<br', s, re.S)]


def _staff_from_site(domain):
    paths = [STAFF_PAGES[domain]] if domain in STAFF_PAGES else ['/sports/football/coaches', '/sports/football/roster']
    best = []
    for path in paths:
        base = f'https://{domain}{path}'
        try:
            res = requests.get(base, headers=ncaa.HEADERS, timeout=15)
        except Exception:
            continue
        if res.status_code != 200:
            continue
        arr = ncaa._nuxt(res.text)
        parsers = ([lambda: ncaa._from_sidearm_data(arr, base), lambda: ncaa._from_wmt_data(arr, base)] if arr else []) + [
            lambda: ncaa._from_sidearm_cards(res.text, base), lambda: ncaa._from_sidearm_classic(res.text, base),
            lambda: ncaa._from_coach_links(res.text, base), lambda: ncaa._from_presto_cards(res.text, base),
            lambda: ncaa._from_staff_table(res.text, base), lambda: _from_coach_rows(res.text, base), lambda: _from_bio_blocks(res.text, base)]
        for parse in parsers:   # pages often carry the staff twice, once cut short: keep the fullest read
            try:
                found = [c for c in parse() if _is_coach(c[1])]
            except Exception:
                continue
            if len(found) > len(best):
                best = found
        if len(best) >= 6:
            break
    out, seen = [], set()
    for name, title, photo in best:
        name = re.sub(r"\s+['’]\d{2}\b", '', re.sub(r'\s+', ' ', name)).strip()
        if name.lower() in seen or re.sub(r'[^a-z]', '', name.lower()) in ('tba', 'tbd'):
            continue
        seen.add(name.lower())
        out.append({'name': name, 'role': re.sub(r'\s+', ' ', title).strip(), 'headshot': photo or None})
    return out


def _coaches(team_id, espn_head):
    domain = TEAMS[team_id][4]
    def load():
        try:
            return _staff_from_site(domain)
        except Exception as e:
            logger.warning(f"FOOTBALL STAFF FAIL | {domain} | {e}")
            return []
    staff = [dict(c) for c in ncaa._cached(f'fbstaff:{domain}', STAFF_SECONDS, load)]
    if espn_head and not any(role_slot(c['role']) == 0 for c in staff):
        staff.insert(0, {'name': espn_head, 'role': 'Head Coach', 'headshot': None})
    staff.sort(key=lambda c: role_slot(c['role']))   # stable: keeps the school's order within each group
    for c in staff:
        c['id'] = re.sub(r'[^a-z0-9]+', '-', c['name'].lower()).strip('-')
        c['slot'] = role_slot(c['role'])
    return staff


def team_data(team_id):
    school, name, color, conf, domain = TEAMS[team_id]
    with ThreadPoolExecutor(2) as pool:
        espn = pool.submit(ncaa._cached, f'fbroster:{team_id}', ROSTER_SECONDS, lambda: _roster(team_id))
        site = pool.submit(ncaa._cached, f'fbsite:{domain}', ROSTER_SECONDS, lambda: _site_roster(domain))
        data, site = espn.result(), site.result()
    players = _merge(_players(data), site)
    players.sort(key=lambda p: (not p['number'].isdigit(), int(p['number']) if p['number'].isdigit() else 0, p['last']))
    head = data.get('coach') or []
    espn_head = f"{head[0].get('firstName', '')} {head[0].get('lastName', '')}".strip() if head else ''
    return {'abbr': team_key(team_id), 'city': school, 'name': name, 'color': color, 'conf': conf,
            'logo': ncaa.logo_url(team_id), 'players': players, 'coaches': _coaches(team_id, espn_head)}


def matchup(away, home):
    with ThreadPoolExecutor(2) as pool:
        teams = list(pool.map(team_data, [away, home]))
    conf = teams[0]['conf'] if teams[0]['conf'] == teams[1]['conf'] else None
    return {'teams': teams, 'sport': 'football',
            'conf_logo': CONFERENCES[conf][1] if conf else ncaa.NCAA_LOGO}
