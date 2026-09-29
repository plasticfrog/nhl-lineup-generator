from flask import Flask, render_template, request, jsonify, send_from_directory, abort, Response, redirect
import pytesseract
from PIL import Image, ImageOps
import requests
import os
import time
import re
import json
import sys
import logging
from datetime import datetime, timezone
from collections import Counter
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from mlb import MLB_TEAMS, fetch_team_data as mlb_fetch_team_data
import activity

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = '/tmp/nhl_uploads'

# Structured logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

activity.init()
app.after_request(activity.ensure_visitor_cookie)

# ALL ORIGINAL TEAM MAPPINGS PRESERVED
TEAM_NAME_MAP = {
    'ANA': 'ducks', 'BOS': 'bruins', 'BUF': 'sabres', 'CAR': 'hurricanes',
    'CBJ': 'bluejackets', 'CGY': 'flames', 'CHI': 'blackhawks', 'COL': 'avalanche',
    'DAL': 'stars', 'DET': 'redwings', 'EDM': 'oilers', 'FLA': 'panthers',
    'LAK': 'kings', 'MIN': 'wild', 'MTL': 'canadiens', 'NJD': 'devils',
    'NSH': 'predators', 'NYI': 'islanders', 'NYR': 'rangers', 'OTT': 'senators',
    'PHI': 'flyers', 'PIT': 'penguins', 'SEA': 'kraken', 'SJS': 'sharks',
    'STL': 'blues', 'TBL': 'lightning', 'TOR': 'mapleleafs', 'UTA': 'utah',
    'VAN': 'canucks', 'VGK': 'goldenknights', 'WPG': 'jets', 'WSH': 'capitals'
}

def get_grid_binned_text(image, rows, cols, debug_label=""):
    """USED BY DUAL SCREENSHOT: Mathematical grid division."""
    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    all_x, all_y, found_words = [], [], []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) >= 1:
            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            found_words.append({'text': text, 'cx': x + w/2, 'cy': y + h/2})
            all_x.extend([x, x + w]); all_y.extend([y, y + h])
    if not found_words: return [""] * (rows * cols)
    min_x, max_x, min_y, max_y = min(all_x), max(all_x), min(all_y), max(all_y)
    grid_w, grid_h = (max_x - min_x) + 1, (max_y - min_y) + 1
    bins = [[] for _ in range(rows * cols)]
    for word in found_words:
        rel_x, rel_y = (word['cx'] - min_x) / grid_w, (word['cy'] - min_y) / grid_h
        col_idx, row_idx = max(0, min(int(rel_x * cols), cols - 1)), max(0, min(int(rel_y * rows), rows - 1))
        # Silhouette filtering for combined Sharks style
        if len(word['text']) <= 2 and word['text'].isdigit() and (rel_y * rows % 1) < 0.2: continue
        bins[(row_idx * cols) + col_idx].append(word['text'])
    return [" ".join(b) for b in bins]

def match_name_to_roster(ocr_text, roster_list, used_names, roster_data_full):
    if not ocr_text: return None

    raw_text = ocr_text.upper()
    # Extract jersey numbers BEFORE character substitutions so 35 doesn't become SS
    found_nums = re.findall(r'\d+', raw_text)

    # Remove stat keywords
    clean_text = re.sub(r'\b(GP|AGE|GP:|AGE:|S:L|S:R|G:|A:|P:|H:|W:)\b', '', raw_text)
    # Apply OCR digit→letter fixes only when digit is adjacent to a letter (not standalone numbers)
    clean_text = re.sub(r'(?<=[A-Z])3|3(?=[A-Z])', 'S', clean_text)
    clean_text = re.sub(r'(?<=[A-Z])5|5(?=[A-Z])', 'S', clean_text)
    clean_text = re.sub(r'(?<=[A-Z])0|0(?=[A-Z])', 'O', clean_text)
    clean_text = re.sub(r'(?<=[A-Z])1|1(?=[A-Z])', 'I', clean_text)

    words = [w for w in clean_text.split() if len(w) > 1]

    best_match, best_score = None, 0
    for roster_name in roster_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        parts = roster_name.split()
        last, first = parts[-1], parts[0]
        score = 0

        # Exact substring match (preserves original behavior)
        if last in clean_text:
            score = 100
        elif words:
            # Fuzzy match last name against OCR words
            best_ratio = max(SequenceMatcher(None, last, w).ratio() for w in words)
            if best_ratio >= 0.8:
                score = 100
            elif best_ratio >= 0.6:
                score = 60  # Weak match — needs first name or number to confirm

        # First name bonus
        if score > 0:
            if first in clean_text:
                score += 100
            elif words:
                best_ratio = max(SequenceMatcher(None, first, w).ratio() for w in words)
                if best_ratio >= 0.7:
                    score += 50

        # Jersey number — strong independent signal
        if p_info.get('number') in found_nums:
            score += 80

        if score > best_score:
            best_score, best_match = score, roster_name

    return best_match if best_score >= 75 else None

def extract_players_from_image(image_file, expected_count, preferred_roster, full_roster, type_label, roster_data_full):
    try:
        image = Image.open(image_file)
        rows, cols = (4, 3) if expected_count == 12 else (3, 2)
        bins = get_grid_binned_text(image, rows, cols, type_label)
        matched, used = [], set()
        for i, text in enumerate(bins):
            m = match_name_to_roster(text, preferred_roster, used, roster_data_full)
            matched.append(m if m else f"PLAYER {i+1}")
            if m: used.add(m)
        return matched[:expected_count]
    except Exception as e: return [f"PLAYER {i+1}" for i in range(expected_count)]

# Picker order, names and colors (color: primary, or secondary where primary is black)
NHL_TEAM_INFO = {
    'ANA': ('Anaheim', 'Ducks', '#F47A38'), 'BOS': ('Boston', 'Bruins', '#FFB81C'),
    'BUF': ('Buffalo', 'Sabres', '#003087'), 'CGY': ('Calgary', 'Flames', '#C8102E'),
    'CAR': ('Carolina', 'Hurricanes', '#CE1126'), 'CHI': ('Chicago', 'Blackhawks', '#CF0A2C'),
    'COL': ('Colorado', 'Avalanche', '#6F263D'), 'CBJ': ('Columbus', 'Blue Jackets', '#002654'),
    'DAL': ('Dallas', 'Stars', '#006847'), 'DET': ('Detroit', 'Red Wings', '#CE1126'),
    'EDM': ('Edmonton', 'Oilers', '#FF4C00'), 'FLA': ('Florida', 'Panthers', '#C8102E'),
    'LAK': ('Los Angeles', 'Kings', '#A2AAAD'), 'MIN': ('Minnesota', 'Wild', '#154734'),
    'MTL': ('Montréal', 'Canadiens', '#AF1E2D'), 'NSH': ('Nashville', 'Predators', '#FFB81C'),
    'NJD': ('New Jersey', 'Devils', '#CE1126'), 'NYI': ('New York', 'Islanders', '#00539B'),
    'NYR': ('New York', 'Rangers', '#0038A8'), 'OTT': ('Ottawa', 'Senators', '#DA1A32'),
    'PHI': ('Philadelphia', 'Flyers', '#F74902'), 'PIT': ('Pittsburgh', 'Penguins', '#FCB514'),
    'SJS': ('San Jose', 'Sharks', '#006D75'), 'SEA': ('Seattle', 'Kraken', '#99D9D9'),
    'STL': ('St. Louis', 'Blues', '#002F87'), 'TBL': ('Tampa Bay', 'Lightning', '#002868'),
    'TOR': ('Toronto', 'Maple Leafs', '#00205B'), 'UTA': ('Utah', 'Mammoth', '#71AFE5'),
    'VAN': ('Vancouver', 'Canucks', '#00843D'), 'VGK': ('Vegas', 'Golden Knights', '#B4975A'),
    'WSH': ('Washington', 'Capitals', '#C8102E'), 'WPG': ('Winnipeg', 'Jets', '#004C97'),
}

ROSTER_CACHE = {}
ROSTER_CACHE_SECONDS = 10 * 60

def fetch_nhl_roster(team):
    """Current roster from the NHL API, cached briefly (the live lineup builder asks often)."""
    cached = ROSTER_CACHE.get(team)
    if cached and time.time() - cached[0] < ROSTER_CACHE_SECONDS:
        return cached[1]
    res = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current", timeout=10)
    res.raise_for_status()
    data = res.json()
    ROSTER_CACHE[team] = (time.time(), data)
    return data

# The NHL's 2026-27 headshots ("latest") are heavily smoothed and look artificial. When a
# player was on the same team last season, use last season's real photo instead (same
# jersey); otherwise fall back to "latest", the only photo showing his current team.
def _previous_season():
    now = datetime.now()
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start - 1}{start}"

PREV_PHOTO_CACHE = {}
PREV_PHOTO_CACHE_SECONDS = 12 * 60 * 60

def _prev_season_photo_ids(team):
    """Player ids on this team's roster last season that have a real photo from that season."""
    cached = PREV_PHOTO_CACHE.get(team)
    if cached and time.time() - cached[0] < PREV_PHOTO_CACHE_SECONDS:
        return cached[1]
    season, ids = _previous_season(), set()
    try:
        data = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/{season}", timeout=8).json()
        candidates = [p['id'] for g in ['forwards', 'defensemen', 'goalies'] for p in data.get(g, [])]

        def has_photo(pid):  # a missing photo redirects to the default silhouette
            try:
                r = requests.head(f"https://assets.nhle.com/mugs/nhl/{season}/{team}/{pid}.png", timeout=5, allow_redirects=False)
                return r.status_code == 200
            except Exception:
                return False

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(16) as pool:
            ids = {pid for pid, ok in zip(candidates, pool.map(has_photo, candidates)) if ok}
    except Exception as e:
        logger.warning(f"PREV SEASON PHOTOS | {team} | {e}")
    PREV_PHOTO_CACHE[team] = (time.time(), ids)
    return ids

def headshot_url(player_id, team=None):
    if team and player_id in _prev_season_photo_ids(team):
        return f"https://assets.nhle.com/mugs/nhl/{_previous_season()}/{team}/{player_id}.png"
    return f"https://assets.nhle.com/mugs/nhl/latest/{player_id}.png"

def friendly_error(e):
    """Plain-English message for errors shown to visitors (the raw error goes to the logs)."""
    text = str(e)
    if isinstance(e, requests.RequestException) or 'Expecting value' in text or 'JSON' in text:
        return "Couldn't reach the NHL's roster service. Give it a minute and try again."
    if 'cannot identify image file' in text:
        return "That file isn't an image we can read. Try a PNG or JPG screenshot."
    return "Something went wrong building that sheet. Try again, and if it keeps happening, try the other method."

def goalie_label(p):
    num = p.get('sweaterNumber')
    last = p['lastName']['default'].upper()
    return f"#{num} {last}" if num else last

def search_player(player_name):
    if not player_name or "PLAYER" in player_name: return None
    try:
        url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={player_name.replace(' ', '%20')}"
        res = requests.get(url, timeout=5).json()
        if res: return {'id': res[0]['playerId'], 'team': res[0]['teamAbbrev'], 'full_name': res[0]['name']}
    except: pass
    return None

# NHL Records API teamId -> team abbreviation
NHL_TEAM_ID_MAP = {
    1: 'NJD', 2: 'NYI', 3: 'NYR', 4: 'PHI', 5: 'PIT', 6: 'BOS', 7: 'BUF',
    8: 'MTL', 9: 'OTT', 10: 'TOR', 12: 'CAR', 13: 'FLA', 14: 'TBL', 15: 'WSH',
    16: 'CHI', 17: 'DET', 18: 'NSH', 19: 'STL', 20: 'CGY', 21: 'COL', 22: 'EDM',
    23: 'VAN', 24: 'ANA', 25: 'DAL', 26: 'LAK', 28: 'SJS', 29: 'CBJ', 30: 'MIN',
    52: 'WPG', 54: 'VGK', 55: 'SEA', 59: 'UTA', 68: 'UTA'
}

BROWSER_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'}

# Each team site keeps its coaching staff at a different path; checked in this order
# (pages with photos first).
STAFF_PAGE_PATHS = ['coaching-staff', 'coaches', 'hockey-operations', 'front-office', 'management',
                    'staff', 'staff-directory', 'hockey-ops', 'hockey-operations-staff']

# Bench coaches only, in the order they fill the 4 coach slots
COACH_ROLE_ORDER = [
    ('HEAD COACH', re.compile(r'^head coach$', re.I)),
    ('ASSOC. COACH', re.compile(r'^associate (head )?coach$', re.I)),
    ('ASST. COACH', re.compile(r'^assistant (head )?coach(es)?$', re.I)),
    ('GOALIE COACH', re.compile(r'^((senior|nhl|director of goaltending,? nhl) )?(goaltending|goalie) coach$', re.I)),
]
COACH_WORD = re.compile(r'\bcoach(es)?\b', re.I)
PERSON_NAME = re.compile(r"^[A-Z][\w'’.\-]+( [A-Z][\w'’.\-]+){1,3}$")
INLINE_SEP = re.compile(r'\s+[|\-–—:]\s+|:\s+')

COACH_CACHE = {}
COACH_CACHE_SECONDS = 6 * 60 * 60

def _norm_name(name):
    import unicodedata
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z]', '', name.lower())

def _same_person(a, b):
    a, b = _norm_name(a), _norm_name(b)
    return bool(a) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.85)

def _coach_slot(role):
    for i, (label, pattern) in enumerate(COACH_ROLE_ORDER):
        if pattern.match(role.strip(' :|-')):
            return i, label
    return None, None

def _square_photo(url):
    """Team sites serve NHL media-CDN images in many crops; ask for a square one."""
    return re.sub(r'(/image/private/)[^/]+/', r'\1t_ratio1_1-size40/', url) if url else ''

def _page_tokens(html):
    """Flatten a staff page into ordered ('txt', text) and ('img', src) tokens."""
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.find('main') or soup.body or soup
    for tag in root.select('script, style, nav, header, footer'):
        tag.decompose()
    tokens = []
    for el in root.descendants:
        if getattr(el, 'name', None) == 'img':
            src = el.get('src') or el.get('data-src') or ''
            if src.startswith('http') and 'logo' not in src.lower():
                tokens.append(('img', src))
        elif isinstance(el, str) and not getattr(el, 'name', None):
            text = ' '.join(el.split())
            if len(text) > 1 and not text.startswith('[if'):
                tokens.append(('txt', text))
    return tokens

def _parse_staff_page(html, head_coach):
    """Return [(role, name, photo_url)] for the NHL coaching staff on a team staff page.

    Handles three layouts: "Name" then "Role", "Role" then "Name", and "Role | Name" /
    "Role - Name" / "Name - Role" on one line. The known head coach (from the Records API)
    tells us which way names and roles are paired; a second "Head Coach" marks the start
    of the AHL affiliate's staff.
    """
    tokens = _page_tokens(html)
    inline = _inline_entries(tokens)
    paired = _paired_entries(tokens, head_coach)
    bench = lambda es: sum(1 for _, r, _ in es if _coach_slot(r)[0] is not None)
    entries = inline if bench(inline) >= bench(paired) else paired  # (token_index, role, name)

    # Keep the NHL staff: from the head coach until the next "Head Coach" (the AHL staff)
    start = next((k for k, (_, r, nm) in enumerate(entries) if _same_person(nm, head_coach)), 0)
    staff, seen_head = [], False
    for k in range(start, len(entries)):
        idx, role, name = entries[k]
        slot, label = _coach_slot(role)
        if label == 'HEAD COACH':
            if seen_head:
                break
            seen_head = True
        # Photo: the image just before this entry, but not past the previous entry
        prev_idx = entries[k - 1][0] if k > 0 else -1
        photo = next((tokens[j][1] for j in range(idx - 1, prev_idx, -1) if tokens[j][0] == 'img'), '')
        staff.append((role, name, photo))

    # A photo shared by several coaches is a placeholder silhouette
    counts = Counter(p for _, _, p in staff if p)
    staff = [(r, n, p if counts[p] == 1 else '') for r, n, p in staff]
    # If only one coach "has" a photo, it's a page banner or someone else's picture
    if sum(1 for _, _, p in staff if p) < 2:
        staff = [(r, n, '') for r, n, _ in staff]
    return staff

def _inline_entries(tokens):
    """'Role | Name', 'Role - Name', 'Name - Role', 'Role: Name1, Name2' on one line."""
    entries = []
    for i, (kind, text) in enumerate(tokens):
        if kind != 'txt' or len(text) > 90 or not COACH_WORD.search(text):
            continue
        parts = INLINE_SEP.split(text, maxsplit=1)
        if len(parts) != 2:
            continue
        left, right = parts[0].strip(), parts[1].strip()
        role, names = (left, right) if COACH_WORD.search(left) else (right, left)
        for name in re.split(r',\s*|\s+and\s+', names):
            if PERSON_NAME.match(name.strip()):
                entries.append((i, role, name.strip()))
    return entries

def _paired_entries(tokens, head_coach):
    """Name and role as separate lines. Which way they pair is read off the head coach."""
    entries = []
    texts = [(i, t) for i, (k, t) in enumerate(tokens) if k == 'txt']
    role_pos = [n for n, (_, t) in enumerate(texts) if len(t) <= 45 and COACH_WORD.search(t)]
    direction = None
    for n, (_, t) in enumerate(texts):
        if head_coach and _same_person(t, head_coach):
            if n + 1 < len(texts) and _coach_slot(texts[n + 1][1])[1] == 'HEAD COACH':
                direction = -1  # name comes before its role
            elif n > 0 and _coach_slot(texts[n - 1][1])[1] == 'HEAD COACH':
                direction = 1   # role comes before its name
            break
    if direction is None:
        before = sum(1 for n in role_pos if n > 0 and PERSON_NAME.match(texts[n - 1][1]))
        after = sum(1 for n in role_pos if n + 1 < len(texts) and PERSON_NAME.match(texts[n + 1][1]))
        direction = -1 if before >= after else 1
    for n in role_pos:
        m = n + direction
        if 0 <= m < len(texts) and PERSON_NAME.match(texts[m][1]) and not COACH_WORD.search(texts[m][1]):
            entries.append((min(texts[n][0], texts[m][0]), texts[n][1], texts[m][1]))
    return entries

def get_coaches_from_nhl(team_abbrev):
    """Head coach from the NHL Records API, plus associate/assistant/goalie coaches (with
    photos when the team site has them) scraped from the team's NHL.com staff page."""
    cached = COACH_CACHE.get(team_abbrev)
    if cached and time.time() - cached[0] < COACH_CACHE_SECONDS:
        return with_saved_photos(cached[1])

    head_coach = ''
    try:
        res = requests.get('https://records.nhl.com/site/api/coach?cayenneExp=isActive=true',
                           headers=BROWSER_HEADERS, timeout=10)
        if res.status_code == 200:
            for c in res.json().get('data', []):
                if NHL_TEAM_ID_MAP.get(c.get('teamId')) == team_abbrev:
                    head_coach = c['fullName']
                    break
    except Exception as e:
        logger.warning(f"NHL coach API error: {e}")

    staff = []
    slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())

    def fetch(path):
        try:
            r = requests.get(f"https://www.nhl.com/{slug}/team/{path}", headers=BROWSER_HEADERS, timeout=8)
            if r.status_code == 200 and 'not-found' not in r.url:
                return r.content
        except Exception:
            pass
        return None

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(len(STAFF_PAGE_PATHS)) as pool:
            pages = list(pool.map(fetch, STAFF_PAGE_PATHS))
        for path, html in zip(STAFF_PAGE_PATHS, pages):
            if not html:
                continue
            found = [s for s in _parse_staff_page(html, head_coach) if _coach_slot(s[0])[0] is not None]
            if len(found) >= 2:
                staff = found
                logger.info(f"NHL COACHES | {team_abbrev} | {path} | {len(found)} coaches")
                break
    except Exception as e:
        logger.warning(f"NHL staff page scrape error: {e}")

    coaches = []
    if head_coach:
        photo = next((p for r, n, p in staff if _same_person(n, head_coach)), '')
        coaches.append({'name': head_coach.upper(), 'role': 'HEAD COACH', 'headshot_url': _square_photo(photo)})
    for role, name, photo in sorted(staff, key=lambda s: _coach_slot(s[0])[0]):
        slot, label = _coach_slot(role)
        if label == 'HEAD COACH' or any(_same_person(name, c['name']) for c in coaches):
            continue
        coaches.append({'name': name.upper(), 'role': label, 'headshot_url': _square_photo(photo)})

    coaches = coaches[:4]
    while len(coaches) < 4:
        coaches.append({'name': '', 'role': 'COACH', 'headshot_url': ''})

    COACH_CACHE[team_abbrev] = (time.time(), coaches)
    return with_saved_photos(coaches)

# Coach photos uploaded from the sheet, shared by everyone. On Railway this lives on a
# volume (Railway sets RAILWAY_VOLUME_MOUNT_PATH when one is attached) so it survives deploys.
COACH_PHOTO_DIR = os.environ.get('COACH_PHOTO_DIR') or os.path.join(
    os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or os.path.dirname(os.path.abspath(__file__)), 'coach_photos')
os.makedirs(COACH_PHOTO_DIR, exist_ok=True)

def coach_photo_slug(name):
    import unicodedata
    name = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def saved_coach_photo_url(name):
    slug = coach_photo_slug(name)
    path = os.path.join(COACH_PHOTO_DIR, f'{slug}.webp')
    if slug and os.path.exists(path):
        return f'/coach_photos/{slug}.webp?v={int(os.path.getmtime(path))}'
    return None

def with_saved_photos(coaches):
    """Uploaded photos win over the ones found on team sites."""
    out = [dict(c) for c in coaches]
    for c in out:
        c['headshot_url'] = saved_coach_photo_url(c['name']) or c['headshot_url']
    return out

def extract_roster_from_screenshot(image_file):
    """Refined for Number Entry method to avoid stat-noise in names"""
    try:
        image = Image.open(image_file); text = pytesseract.image_to_string(image)
        roster = {}
        for line in text.split('\n'):
            line = line.strip()
            # Look for number at the start, followed by the rest of the line as name
            match = re.search(r'^(\d+)\s+(.+)', line)
            if match:
                num, name = match.groups()
                # Clean name: remove common table stats that might follow name
                name = re.split(r'\d', name)[0].strip().upper()
                if len(name) > 3: roster[num] = {'name': name}
        return roster, []
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    """Finds all digits in the provided input"""
    if text: return re.findall(r'\d+', text)
    if image_file:
        try: return re.findall(r'\d+', pytesseract.image_to_string(Image.open(image_file)))
        except: return []
    return []

@app.route('/')
def index():
    teams = [{'abbr': a, 'city': c, 'name': n, 'color': col} for a, (c, n, col) in NHL_TEAM_INFO.items()]
    return render_template('index.html', teams=teams)

@app.route('/numbers')
def numbers_page():
    return redirect('/?mode=numbers')

@app.route('/api/nhl/roster/<team>')
def api_nhl_roster(team):
    team = team.upper()
    if team not in NHL_TEAM_INFO:
        return jsonify({'error': 'Unknown team'}), 404
    try:
        data = fetch_nhl_roster(team)
    except Exception as e:
        logger.warning(f"NHL ROSTER FAIL | {team} | {e}")
        return jsonify({'error': friendly_error(e)}), 502
    out = {}
    for group in ['forwards', 'defensemen', 'goalies']:
        out[group] = [{
            'number': str(p['sweaterNumber']) if p.get('sweaterNumber') is not None else '',
            'first': p['firstName']['default'], 'last': p['lastName']['default'],
            'pos': p.get('positionCode', ''), 'headshot': headshot_url(p['id'], team),
        } for p in data.get(group, [])]
    return jsonify(out)

@app.route('/api/nhl/read_numbers', methods=['POST'])
def api_read_numbers():
    """Read jersey numbers off a screenshot (for the lineup builder)."""
    image = request.files.get('image')
    if not image:
        return jsonify({'error': 'No image'}), 400
    return jsonify({'numbers': extract_line_numbers(image_file=image)})

@app.route('/lineup')
def lineup(): return render_template('lineup.html')

@app.route('/process', methods=['POST'])
def process_lineup():
    start = time.time()
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    method = 'combined' if request.files.get('combined') else 'split'
    logger.info(f"NHL PROCESS | method: {method} | IP: {ip}")
    try:
        comb = request.files.get('combined')
        f_file, d_file = request.files.get('forwards'), request.files.get('defense')
        sample = comb if comb else f_file
        chosen = (request.form.get('team') or '').upper()
        if chosen in NHL_TEAM_INFO:
            team = chosen
        else:
            sample.seek(0); img_full = Image.open(sample)
            test_data = pytesseract.image_to_string(img_full)
            found_teams = []
            for word in test_data.split():
                if len(word) > 4:
                    res = search_player(word)
                    if res and res['team']: found_teams.append(res['team'])
            if not found_teams:
                elapsed = round(time.time() - start, 2)
                activity.record('nhl_screenshot', 'Unknown team', False, elapsed, 'Could not detect the team')
                return jsonify({'error': "We couldn't tell which team this is. Pick the team above and try again."}), 422
            team = Counter(found_teams).most_common(1)[0][0]
        
        r_json = fetch_nhl_roster(team)
        roster_data_full, goalies = {}, []
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos_key == 'goalies':
                    goalies.append({'name': goalie_label(p), 'id': p['id'], 'last': p['lastName']['default'].upper()})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p.get('sweaterNumber', '')), 'is_forward': pos_key=='forwards'}

        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        if comb:
            comb.seek(0); img_c = Image.open(comb); w, h = img_c.size
            d_ocr = pytesseract.image_to_data(img_c, output_type=pytesseract.Output.DICT)
            f_start, d_start, d_end, g_col_x = 0, int(h * 0.55), h, int(w * 0.65)
            for i, txt in enumerate(d_ocr['text']):
                u_txt = txt.upper()
                if 'FORWARDS' in u_txt: f_start = d_ocr['top'][i] + d_ocr['height'][i]
                if 'DEFENSE' in u_txt: d_start = d_ocr['top'][i] + d_ocr['height'][i]
                if 'GOALTENDER' in u_txt: g_col_x = d_ocr['left'][i]
                if 'COACH' in u_txt or 'SCRATCH' in u_txt: d_end = min(d_end, d_ocr['top'][i])

            f_zone = img_c.crop((0, f_start + 15, w, d_start - 15))
            f_bins = get_grid_binned_text(f_zone, 4, 3, "COMBINED-FORWARDS")
            forwards_raw, used_f = [], set()
            for i, txt in enumerate(f_bins):
                m = match_name_to_roster(txt, f_names, used_f, roster_data_full)
                forwards_raw.append(m if m else f"PLAYER {i+1}")
                if m: used_f.add(m)

            d_zone = img_c.crop((0, d_start + 15, g_col_x - 10, d_end - 15))
            d_bins = get_grid_binned_text(d_zone, 3, 2, "COMBINED-DEFENSE")
            defense_raw, used_d = [], set()
            for i, txt in enumerate(d_bins):
                m = match_name_to_roster(txt, d_names, used_d, roster_data_full)
                defense_raw.append(m if m else f"PLAYER {i+13}")
                if m: used_d.add(m)
        else:
            f_file.seek(0); d_file.seek(0)
            forwards_raw = extract_players_from_image(f_file, 12, f_names, list(roster_data_full.keys()), "FORWARDS", roster_data_full)
            defense_raw = extract_players_from_image(d_file, 6, d_names, list(roster_data_full.keys()), "DEFENSE", roster_data_full)

        final_f, final_d = [], []
        for n in forwards_raw:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_f.append({'name': n, 'number': info['number'], 'is_forward': True, 'headshot_url': headshot_url(info['id'], team) if info['id'] else None})
        for n in defense_raw:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_d.append({'name': n, 'number': info['number'], 'is_forward': False, 'headshot_url': headshot_url(info['id'], team) if info['id'] else None})

        elapsed = round(time.time() - start, 2)
        matched_f = sum(1 for p in final_f if 'PLAYER' not in p['name'])
        matched_d = sum(1 for p in final_d if 'PLAYER' not in p['name'])
        logger.info(f"NHL PROCESS OK | team: {team} | method: {method} | {elapsed}s | matched: {matched_f}F+{matched_d}D of {len(final_f)}F+{len(final_d)}D")
        activity.record('nhl_screenshot', team, True, elapsed,
                        f"{matched_f + matched_d}/{len(final_f) + len(final_d)} players matched · {'one' if method == 'combined' else 'two'} screenshot{'s' if method != 'combined' else ''}")
        return jsonify({'forwards': final_f, 'defensemen': final_d, 'goalies': [{'name': g['name'], 'headshot_url': headshot_url(g['id'], team)} for g in goalies[:2]], 'coaches': get_coaches_from_nhl(team), 'team': team,
                        'matched': matched_f + matched_d, 'total': len(final_f) + len(final_d)})
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"NHL PROCESS FAIL | method: {method} | {elapsed}s | error: {e}")
        activity.record('nhl_screenshot', 'Unknown team', False, elapsed, str(e)[:300])
        return jsonify({'error': friendly_error(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    """Build a lineup from jersey numbers. Names always come from the official NHL roster.

    New form: slots = JSON {"forwards": [12 numbers], "defense": [6], "goalies": [2]} where
    blanks ("") keep their place on the sheet. Old form: lines_text / lines_screenshot, read in
    order (first 12 forwards, next 6 D); roster_screenshot is optional and only overrides names.
    """
    start = time.time()
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    team = None
    try:
        team = (request.form.get('team') or '').upper()
        logger.info(f"NHL NUMBERS | team: {team} | IP: {ip}")
        if team not in NHL_TEAM_INFO:
            activity.record('nhl_numbers', team or 'No team', False, round(time.time() - start, 2), 'No team picked')
            return jsonify({'error': 'Pick a team first.'}), 400
        api = fetch_nhl_roster(team)

        by_number = {}
        for pos in ['forwards', 'defensemen', 'goalies']:
            for p in api.get(pos, []):
                if p.get('sweaterNumber') is None: continue  # unsigned/camp players have no number yet
                by_number[str(p['sweaterNumber'])] = p

        slots = request.form.get('slots')
        if slots:
            slots = json.loads(slots)
            f_nums = [str(n).strip() for n in slots.get('forwards', [])][:12]
            d_nums = [str(n).strip() for n in slots.get('defense', [])][:6]
            g_nums = [str(n).strip() for n in slots.get('goalies', [])][:2]
            ref_roster = {}
        else:
            ref_roster, _ = extract_roster_from_screenshot(request.files.get('roster_screenshot'))
            raw = extract_line_numbers(text=request.form.get('lines_text'), image_file=request.files.get('lines_screenshot'))
            nums = [n for n in raw if n in by_number and by_number[n] not in api.get('goalies', [])][:18]
            f_nums, d_nums, g_nums = nums[:12], nums[12:18], []

        def card(n, slot_no, is_forward):
            p = by_number.get(n)
            if not p:
                return {'name': f"PLAYER {slot_no}", 'number': '', 'is_forward': is_forward, 'headshot_url': None}
            name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
            scr_name = ref_roster.get(n, {}).get('name', '')
            return {'name': scr_name if scr_name and not scr_name.isdigit() else name, 'number': n,
                    'is_forward': is_forward, 'headshot_url': headshot_url(p['id'], team)}

        f_out = [card(n, i + 1, True) for i, n in enumerate(f_nums)]
        d_out = [card(n, i + 13, False) for i, n in enumerate(d_nums)]

        goalies = [by_number[n] for n in g_nums if n in by_number]
        for p in api.get('goalies', []):  # fill any open goalie slot from the roster
            if len(goalies) >= 2: break
            if p not in goalies: goalies.append(p)

        filled = sum(1 for c in f_out + d_out if c['number'])
        elapsed = round(time.time() - start, 2)
        logger.info(f"NHL NUMBERS OK | team: {team} | {elapsed}s | players: {len(f_out)}F+{len(d_out)}D")
        activity.record('nhl_numbers', team, True, elapsed, f"{filled} players from jersey numbers")
        return jsonify({
            'forwards': f_out, 'defensemen': d_out,
            'goalies': [{'name': goalie_label(p), 'headshot_url': headshot_url(p['id'], team)} for p in goalies[:2]],
            'coaches': get_coaches_from_nhl(team), 'team': team, 'matched': filled, 'total': len(f_out) + len(d_out)
        })
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"NHL NUMBERS FAIL | team: {team} | {elapsed}s | error: {e}")
        activity.record('nhl_numbers', team or 'Unknown team', False, elapsed, str(e)[:300])
        return jsonify({'error': friendly_error(e)}), 500

@app.route('/coach_photos', methods=['GET'])
def list_coach_photos():
    photos = {}
    for f in os.listdir(COACH_PHOTO_DIR):
        if f.endswith('.webp'):
            slug = f[:-5]
            photos[slug] = f'/coach_photos/{f}?v={int(os.path.getmtime(os.path.join(COACH_PHOTO_DIR, f)))}'
    return jsonify(photos)

@app.route('/coach_photos/<slug>.webp')
def coach_photo(slug):
    if not re.fullmatch(r'[a-z0-9-]+', slug):
        abort(404)
    return send_from_directory(COACH_PHOTO_DIR, f'{slug}.webp', max_age=3600)

@app.route('/coach_photos', methods=['POST'])
def upload_coach_photo():
    """Save a coach photo for everyone. Form fields: name, photo (image file)."""
    name = (request.form.get('name') or '').strip()
    file = request.files.get('photo')
    slug = coach_photo_slug(name)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if not slug or not file:
        return jsonify({'error': 'Need a coach name and a photo'}), 400
    try:
        # Re-encoding through Pillow also guarantees we only ever store a plain image
        img = ImageOps.exif_transpose(Image.open(file.stream)).convert('RGBA')
        img.thumbnail((400, 400))
        img.save(os.path.join(COACH_PHOTO_DIR, f'{slug}.webp'), 'WEBP', quality=88)
    except Exception as e:
        logger.warning(f"COACH PHOTO FAIL | {name} | IP: {ip} | error: {e}")
        activity.record('coach_photo', name.upper(), False, None, 'Not a readable image')
        return jsonify({'error': 'That file is not an image we can read'}), 400
    logger.info(f"COACH PHOTO SAVED | {name} | IP: {ip}")
    activity.record('coach_photo', name.upper(), True, None, 'Photo saved for everyone')
    return jsonify({'name': name.upper(), 'url': saved_coach_photo_url(name),
                    'persistent': bool(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or os.environ.get('COACH_PHOTO_DIR'))})

@app.route('/mlb')
def mlb_select():
    teams_sorted = sorted(MLB_TEAMS.items(), key=lambda x: x[1]['name'])
    return render_template('mlb_select.html', teams=teams_sorted)

@app.route('/mlb/generate', methods=['POST'])
def mlb_generate():
    start = time.time()
    away_slug = request.form.get('away_team')
    home_slug = request.form.get('home_team')
    away_name = MLB_TEAMS.get(away_slug, {}).get('name', away_slug)
    home_name = MLB_TEAMS.get(home_slug, {}).get('name', home_slug)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', 'unknown')

    logger.info(f"MLB GENERATE | {away_name} @ {home_name} | IP: {ip} | UA: {ua}")

    try:
        away_data = mlb_fetch_team_data(away_slug, MLB_TEAMS[away_slug]['id'])
        home_data = mlb_fetch_team_data(home_slug, MLB_TEAMS[home_slug]['id'])
        elapsed = round(time.time() - start, 2)
        count = lambda d: sum(1 for p in d.get('pitchers', []) + d.get('pos_players', []) if p.get('name'))
        away_players, home_players = count(away_data), count(home_data)
        away_coaches = len(away_data.get('coaches', []))
        home_coaches = len(home_data.get('coaches', []))
        logger.info(f"MLB GENERATE OK | {away_name} @ {home_name} | {elapsed}s | players: {away_players}+{home_players} | coaches: {away_coaches}+{home_coaches}")
        activity.record('mlb', f"{away_name} @ {home_name}", True, elapsed,
                        f"{away_players}+{home_players} players · {away_coaches}+{home_coaches} coaches")
        return jsonify({'teams': [away_data, home_data]})
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"MLB GENERATE FAIL | {away_name} @ {home_name} | {elapsed}s | error: {e}")
        activity.record('mlb', f"{away_name} @ {home_name}", False, elapsed, str(e)[:300])
        return jsonify({'error': "Couldn't build those sheets. MLB's roster service may be slow; give it a minute and try again."}), 500

@app.route('/mlb/sheet')
def mlb_sheet():
    return render_template('mlb_sheet.html')

# ---- Activity dashboard (password: ADMIN_PASSWORD env var; username can be anything) ----

def _admin_ok():
    password = os.environ.get('ADMIN_PASSWORD')
    auth = request.authorization
    return bool(password) and auth is not None and auth.password == password

def _admin_denied():
    if not os.environ.get('ADMIN_PASSWORD'):
        return Response('Activity page is off: set ADMIN_PASSWORD in Railway variables to turn it on.', 503)
    return Response('Password required', 401, {'WWW-Authenticate': 'Basic realm="Lineup activity"'})

@app.route('/activity/event', methods=['POST'])
def activity_event():
    """Sheet pages report prints here."""
    kind = request.form.get('kind')
    if kind in ('print_nhl', 'print_mlb'):
        activity.record(kind, (request.form.get('summary') or '')[:80] or 'Sheet', True)
    return ('', 204)

@app.route('/activity')
def activity_page():
    if not _admin_ok():
        return _admin_denied()
    return render_template('activity.html')

@app.route('/activity/data')
def activity_data():
    if not _admin_ok():
        return _admin_denied()
    days = min(int(request.args.get('days', 30)), 365)
    return jsonify(activity.dashboard_data(days))

@app.route('/activity/name', methods=['POST'])
def activity_name():
    if not _admin_ok():
        return _admin_denied()
    activity.set_visitor_name(request.form.get('visitor', ''), (request.form.get('name') or '').strip())
    return ('', 204)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
