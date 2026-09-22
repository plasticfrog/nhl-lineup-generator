from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
import time
import re
import sys
import logging
from datetime import datetime, timezone
from collections import Counter
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from mlb import MLB_TEAMS, fetch_team_data as mlb_fetch_team_data

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
        return [dict(c) for c in cached[1]]

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
    return [dict(c) for c in coaches]

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
def index(): return render_template('index.html')

@app.route('/numbers')
def numbers_page(): return render_template('index_numbers.html')

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
        sample.seek(0); img_full = Image.open(sample)
        test_data = pytesseract.image_to_string(img_full)
        found_teams = []
        for word in test_data.split():
            if len(word) > 4:
                res = search_player(word)
                if res and res['team']: found_teams.append(res['team'])
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
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
            final_f.append({'name': n, 'number': info['number'], 'is_forward': True, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})
        for n in defense_raw:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_d.append({'name': n, 'number': info['number'], 'is_forward': False, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})

        elapsed = round(time.time() - start, 2)
        matched_f = sum(1 for p in final_f if 'PLAYER' not in p['name'])
        matched_d = sum(1 for p in final_d if 'PLAYER' not in p['name'])
        logger.info(f"NHL PROCESS OK | team: {team} | method: {method} | {elapsed}s | matched: {matched_f}F+{matched_d}D of {len(final_f)}F+{len(final_d)}D")
        return jsonify({'forwards': final_f, 'defensemen': final_d, 'goalies': [{'name': g['name'], 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{g['id']}.png"} for g in goalies[:2]], 'coaches': get_coaches_from_nhl(team), 'team': team})
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"NHL PROCESS FAIL | method: {method} | {elapsed}s | error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    """Improved to strictly use Official API names and ignore stat-noise digits"""
    start = time.time()
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    try:
        team = request.form.get('team'); roster_img = request.files.get('roster_screenshot')
        logger.info(f"NHL NUMBERS | team: {team} | IP: {ip}")
        l_text, l_img = request.form.get('lines_text'), request.files.get('lines_screenshot')
        
        ref_roster, _ = extract_roster_from_screenshot(roster_img)
        raw_nums = extract_line_numbers(text=l_text, image_file=l_img)
        
        api = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        
        # Build a valid skater map to filter out stat-noise digits (like Age, GP)
        valid_skaters = {}
        for pos in ['forwards', 'defensemen']:
            for p in api.get(pos, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if 'sweaterNumber' not in p: continue  # unsigned/camp players have no number yet
                valid_skaters[str(p['sweaterNumber'])] = {'id': p['id'], 'name': name, 'is_forward': pos == 'forwards'}
        
        # Filter: Only keep numbers that actually belong to an active player on the team
        nums = [n for n in raw_nums if n in valid_skaters]
        
        f_out, d_out = [], []
        for i, n in enumerate(nums[:18]):
            player = valid_skaters[n]
            # Use Official Name if screenshot name is missing or just digits
            scr_name = ref_roster.get(n, {}).get('name', '')
            display_name = player['name'] if not scr_name or scr_name.isdigit() else scr_name
            
            obj = {
                'name': display_name, 'number': n, 'is_forward': i < 12,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{player['id']}.png"
            }
            if i < 12: f_out.append(obj)
            else: d_out.append(obj)
            
        elapsed = round(time.time() - start, 2)
        logger.info(f"NHL NUMBERS OK | team: {team} | {elapsed}s | players: {len(f_out)}F+{len(d_out)}D")
        return jsonify({
            'forwards': f_out, 'defensemen': d_out,
            'goalies': [{'name': goalie_label(p), 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{p['id']}.png"} for p in api.get('goalies', [])][:2],
            'coaches': get_coaches_from_nhl(team), 'team': team
        })
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"NHL NUMBERS FAIL | team: {team} | {elapsed}s | error: {e}")
        return jsonify({'error': str(e)}), 500

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
        away_players = len(away_data.get('players', []))
        home_players = len(home_data.get('players', []))
        away_coaches = len(away_data.get('coaches', []))
        home_coaches = len(home_data.get('coaches', []))
        logger.info(f"MLB GENERATE OK | {away_name} @ {home_name} | {elapsed}s | players: {away_players}+{home_players} | coaches: {away_coaches}+{home_coaches}")
        return jsonify({'teams': [away_data, home_data]})
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"MLB GENERATE FAIL | {away_name} @ {home_name} | {elapsed}s | error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/mlb/sheet')
def mlb_sheet():
    return render_template('mlb_sheet.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
