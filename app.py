from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
import time
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from mlb import MLB_TEAMS, fetch_team_data as mlb_fetch_team_data

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = '/tmp/nhl_uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ALL ORIGINAL TEAM MAPPINGS PRESERVED
TEAM_NAME_MAP = {
    'ANA': 'ducks', 'BOS': 'bruins', 'BUF': 'sabres', 'CAR': 'hurricanes',
    'CBJ': 'bluejackets', 'CGY': 'flames', 'CHI': 'blackhawks', 'COL': 'avalanche',
    'DAL': 'stars', 'DET': 'redwings', 'EDM': 'oilers', 'FLA': 'panthers',
    'LAK': 'kings', 'MIN': 'wild', 'MTL': 'canadiens', 'NJD': 'devils',
    'NSH': 'predators', 'NYI': 'islanders', 'NYR': 'rangers', 'OTT': 'senators',
    'PHI': 'flyers', 'PIT': 'penguins', 'SEA': 'kraken', 'SJS': 'sharks',
    'STL': 'blues', 'TBL': 'lightning', 'TOR': 'mapleleafs', 'UTA': 'utahhockeyclub',
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

def match_name_to_roster(ocr_text, roster_list, used_names, roster_data_full, debug_log=None):
    if not ocr_text:
        if debug_log is not None: debug_log.append({'cell_text': '', 'result': None, 'reason': 'empty cell'})
        return None

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
    top_candidates = []
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

        if score > 0:
            top_candidates.append({'name': roster_name, 'score': score})

        if score > best_score:
            best_score, best_match = score, roster_name

    result = best_match if best_score >= 75 else None
    if debug_log is not None:
        top_candidates.sort(key=lambda x: x['score'], reverse=True)
        debug_log.append({
            'raw_ocr': raw_text,
            'clean_text': clean_text,
            'words': words,
            'found_nums': found_nums,
            'best_match': result,
            'best_score': best_score,
            'top_3': top_candidates[:3]
        })
    return result

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

def search_player(player_name):
    if not player_name or "PLAYER" in player_name: return None
    try:
        url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={player_name.replace(' ', '%20')}"
        res = requests.get(url, timeout=5).json()
        if res: return {'id': res[0]['playerId'], 'team': res[0]['teamAbbrev'], 'full_name': res[0]['name']}
    except: pass
    return None

def get_coaches_from_nhl(team_abbrev):
    try:
        slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        res = requests.get(f"https://www.nhl.com/{slug}/team/coaches", headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        coaches = []
        for img in soup.find_all('img'):
            alt = img.get('alt', '').upper()
            if 'COACH' in alt:
                coaches.append({'name': alt.split('-')[0].strip(), 'role': 'COACH', 'headshot_url': img.get('src')})
                if len(coaches) >= 4: break
        return coaches
    except: return []

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
                    goalies.append({'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'id': p['id'], 'last': p['lastName']['default'].upper()})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos_key=='forwards'}

        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        debug_info = {'team_detected': team, 'sections': {}, 'forward_cells': [], 'defense_cells': []}

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

            debug_info['sections'] = {'f_start': f_start, 'd_start': d_start, 'd_end': d_end, 'g_col_x': g_col_x, 'img_w': w, 'img_h': h}

            f_zone = img_c.crop((0, f_start + 15, w, d_start - 15))
            f_bins = get_grid_binned_text(f_zone, 4, 3, "COMBINED-FORWARDS")
            forwards_raw, used_f = [], set()
            f_debug = []
            for i, txt in enumerate(f_bins):
                m = match_name_to_roster(txt, f_names, used_f, roster_data_full, debug_log=f_debug)
                forwards_raw.append(m if m else f"PLAYER {i+1}")
                if m: used_f.add(m)
            debug_info['forward_cells'] = f_debug

            d_zone = img_c.crop((0, d_start + 15, g_col_x - 10, d_end - 15))
            d_bins = get_grid_binned_text(d_zone, 3, 2, "COMBINED-DEFENSE")
            defense_raw, used_d = [], set()
            d_debug = []
            for i, txt in enumerate(d_bins):
                m = match_name_to_roster(txt, d_names, used_d, roster_data_full, debug_log=d_debug)
                defense_raw.append(m if m else f"PLAYER {i+13}")
                if m: used_d.add(m)
            debug_info['defense_cells'] = d_debug
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

        return jsonify({'forwards': final_f, 'defensemen': final_d, 'goalies': [{'name': g['name'], 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{g['id']}.png"} for g in goalies[:2]], 'coaches': get_coaches_from_nhl(team), 'team': team, '_debug': debug_info})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    """Improved to strictly use Official API names and ignore stat-noise digits"""
    try:
        team = request.form.get('team'); roster_img = request.files.get('roster_screenshot')
        l_text, l_img = request.form.get('lines_text'), request.files.get('lines_screenshot')
        
        ref_roster, _ = extract_roster_from_screenshot(roster_img)
        raw_nums = extract_line_numbers(text=l_text, image_file=l_img)
        
        api = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        
        # Build a valid skater map to filter out stat-noise digits (like Age, GP)
        valid_skaters = {}
        for pos in ['forwards', 'defensemen']:
            for p in api.get(pos, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
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
            
        return jsonify({
            'forwards': f_out, 'defensemen': d_out,
            'goalies': [{'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{p['id']}.png"} for p in api.get('goalies', [])][:2],
            'coaches': get_coaches_from_nhl(team), 'team': team
        })
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/mlb')
def mlb_select():
    teams_sorted = sorted(MLB_TEAMS.items(), key=lambda x: x[1]['name'])
    return render_template('mlb_select.html', teams=teams_sorted)

@app.route('/mlb/generate', methods=['POST'])
def mlb_generate():
    try:
        away_slug = request.form.get('away_team')
        home_slug = request.form.get('home_team')
        away_data = mlb_fetch_team_data(away_slug, MLB_TEAMS[away_slug]['id'])
        home_data = mlb_fetch_team_data(home_slug, MLB_TEAMS[home_slug]['id'])
        return jsonify({'teams': [away_data, home_data]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/mlb/sheet')
def mlb_sheet():
    return render_template('mlb_sheet.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
