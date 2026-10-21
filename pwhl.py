"""PWHL teams and rosters from HockeyTech, the stats feed behind thepwhl.com."""
import io
import logging
import time
from collections import Counter

import requests
from PIL import Image

logger = logging.getLogger(__name__)

FEED = 'https://lscluster.hockeytech.com/feed/index.php'
# Public key/client code that thepwhl.com's own stats pages use
FEED_PARAMS = {'key': '446521baf8c38984', 'client_code': 'pwhl', 'fmt': 'json', 'lang': 'en'}
NO_PHOTO = 'nophoto'
CACHE_SECONDS = 10 * 60

_cache = {}


def _cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _feed(view, **params):
    res = requests.get(FEED, params={'feed': 'modulekit', 'view': view, **FEED_PARAMS, **params}, timeout=10)
    res.raise_for_status()
    return res.json()['SiteKit']


def seasons():
    """Season ids, newest first (regular season, preseason and playoffs)."""
    return _cached('seasons', 6 * 3600, lambda: [s['season_id'] for s in _feed('seasons')['Seasons']])


def _logo_color(url):
    """The logo's main color, used for the team's name bars (new teams have no brand guide yet)."""
    try:
        img = Image.open(io.BytesIO(requests.get(url, timeout=8).content)).convert('RGBA').resize((80, 80))
        counts = Counter()
        for r, g, b, a in img.getdata():
            hi, lo = max(r, g, b), min(r, g, b)
            if a < 200 or hi > 235 and lo > 200 or hi < 40 or hi - lo < 40:  # skip clear, white, black, grey
                continue
            counts[(r // 24 * 24, g // 24 * 24, b // 24 * 24)] += 1
        if counts:
            r, g, b = counts.most_common(1)[0][0]
            return f'#{min(r + 12, 255):02X}{min(g + 12, 255):02X}{min(b + 12, 255):02X}'
    except Exception as e:
        logger.warning(f'PWHL logo color | {url} | {e}')
    return '#4B2A85'  # PWHL purple


# Where the logo's main color isn't the team's color
COLOR_OVERRIDES = {'MTL': '#862633', 'TOR': '#1F4E9C', 'DET': '#C8102E', 'MIN': '#5B3A8E'}


def teams():
    """{abbr: {id, name, city, logo, color}} for the newest season, in alphabetical order."""
    def load():
        out = {}
        for t in _feed('teamsbyseason', season_id=seasons()[0])['Teamsbyseason']:
            out[t['code']] = {'id': t['id'], 'name': t['name'], 'city': t.get('city', ''), 'logo': t['team_logo_url'],
                              'color': COLOR_OVERRIDES.get(t['code']) or _logo_color(t['team_logo_url'])}
        return dict(sorted(out.items(), key=lambda kv: kv[1]['name']))
    return _cached('teams', 6 * 3600, load)


def roster(abbr):
    """Players grouped like the NHL roster: forwards / defensemen / goalies.

    Uses the newest season that has players on it (regular-season rosters stay empty until
    just before opening night, so the preseason roster is used until then).
    """
    team = teams()[abbr]

    def load():
        for season in seasons():
            players = [p for p in _feed('roster', team_id=team['id'], season_id=season)['Roster']
                       if isinstance(p, dict) and p.get('player_id')]
            if players:
                break
        photos = _real_photos([p.get('player_image') or '' for p in players])
        groups = {'forwards': [], 'defensemen': [], 'goalies': []}
        for p in players:
            pos = (p.get('position') or '').upper()
            group = 'goalies' if pos == 'G' else 'defensemen' if pos in ('D', 'LD', 'RD') else 'forwards'
            image = p.get('player_image') or ''
            groups[group].append({
                'id': p['player_id'],
                'first': p.get('first_name', ''), 'last': p.get('last_name', ''),
                'number': str(p.get('tp_jersey_number') or '').strip(),
                'pos': pos,
                'headshot': image if image in photos else None,
            })
        return groups
    return _cached(f'roster:{abbr}', CACHE_SECONDS, load)


# Players without a real photo get a PWHL-logo placeholder at their own photo address. Every
# placeholder is the same file, while real photos are unique, so an image shared by two or
# more players (or one we've already seen) is the placeholder and should show a silhouette.
PLACEHOLDER_HASHES = {'e97122140ef2040e58fd0b2de69887f6'}


def _real_photos(urls):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    urls = [u for u in urls if u and NO_PHOTO not in u]

    def fingerprint(url):
        def fetch():
            try:
                res = requests.get(url, timeout=8)
                return hashlib.md5(res.content).hexdigest() if res.ok else None
            except Exception:
                return 'unknown'  # can't check: keep the photo
        # Remembered for a few hours, so a newly uploaded photo replaces the placeholder same day
        return _cached(f'photo:{url}', 6 * 3600, fetch)

    with ThreadPoolExecutor(16) as pool:
        prints = dict(zip(urls, pool.map(fingerprint, urls)))
    counts = Counter(h for h in prints.values() if h and h != 'unknown')
    PLACEHOLDER_HASHES.update(h for h, n in counts.items() if n > 1)
    return {u for u, h in prints.items() if h and h not in PLACEHOLDER_HASHES}
