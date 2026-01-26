from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
import time
import re
import sys
from collections import Counter
from bs4 import BeautifulSoup

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

def get_grid_binned_text(image, rows, cols):
    """
    Mathematical Grid Sorting: 
    Divides the content area into a strict R x C grid to ensure Left-Center-Right order.
    """
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    
    all_x, all_y, found_words = [], [], []
    
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) >= 1:
            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            found_words.append({'text': text, 'cx': x + w/2, 'cy': y + h/2})
            all_x.extend([x, x + w]); all_y.extend([y, y + h])
            
    if not found_words: return [""] * (rows * cols)

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    grid_w, grid_h = (max_x - min_x) + 1, (max_y - min_y) + 1
    
    bins = [[] for _ in range(rows * cols)]
    
    for word in found_words:
        rel_x = (word['cx'] - min_x) / grid_w
        rel_y = (word['cy'] - min_y) / grid_h
        
        col_idx = int(rel_x * cols)
        row_idx = int(rel_y * rows)
        
        col_idx = max(0, min(col_idx, cols - 1))
        row_idx = max(0, min(row_idx, rows - 1))
        
        bin_idx = (row_idx * cols) + col_idx
        bins[bin_idx].append(word['text'])

    return [" ".join(b) for b in bins]

def match_name_to_roster(ocr_text, full_team_list, preferred_list, used_names, roster_data_full):
    if not ocr_text: return None
    clean_text = ocr_text.upper().replace('3', 'S').replace('5', 'S').replace('0', 'O').replace('1', 'I')
    found_nums = re.findall(r'\d+', clean_text)
    
    # 1. Check Preferred List
    for roster_name in preferred_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        last = roster_name.split()[-1]
        first = roster_name.split()[0]
        if (first in clean_text and last in clean_text) or (p_info.get('number') in found_nums and last in clean_text):
            return roster_name

    # 2. Check Full Team
    for roster_name in full_team_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        last = roster_name.split()[-1]
        first = roster_name.split()[0]
        if (first in clean_text and last in clean_text) or (p_info.get('number') in found_nums and last in clean_text):
            return roster_name

    # 3. Last Name Backup
    for roster_name in full_team_list:
        if roster_name in used_names: continue
        if roster_name.split()[-1] in clean_text:
            return roster_name
    return None

def extract_players_from_image(image_file, expected_count, preferred_roster, full_roster, type_label, roster_data_full):
    """USED BY DUAL SCREENSHOT METHOD"""
    try:
        print(f"\n=== PROCESSING SEPARATE {type_label} IMAGE ===", flush=True)
        image = Image.open(image_file)
        rows = 4 if expected_count == 12 else 3
        cols = 3 if expected_count == 12 else 2
        
        bins = get_grid_binned_text(image, rows, cols)
        matched_names, used = [], set()
        for i, bin_text in enumerate(bins):
            match = match_name_to_roster(bin_text, full_roster, preferred_roster, used, roster_data_full)
            matched_names.append(match if match else f"PLAYER {i+1}")
            if match: used.add(match)
        return matched_names[:expected_count]
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

# ORIGINAL SEARCH, SCRAPING, AND UTILITIES PRESERVED
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
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image)
        roster = {}
        for line in text.split('\n'):
            line = line.strip()
            nums = re.findall(r'^\d+', line)
            if nums:
                num = nums[0]
                name = line.replace(num, '').strip().upper()
                roster[num] = {'name': name}
        return roster, []
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    if text: return re.findall(r'\d+', text)
    if image_file:
        try:
            return re.findall(r'\d+', pytesseract.image_to_string(Image.open(image_file)))
        except: return []
    return []

# ROUTES
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
        
        # 1. Determine Team
        sample = comb if comb else f_file
        sample.seek(0)
        img_full = Image.open(sample)
        test_data = pytesseract.image_to_string(img_full)
        found_teams = []
        for word in test_data.split():
            if len(word) > 4:
                res = search_player(word)
                if res and res['team']: found_teams.append(res['team'])
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        # 2. Get Roster
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        roster_data_full, goalies = {}, []
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos_key == 'goalies':
                    goalies.append({'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{p['id']}.png"})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos_key=='forwards'}

        all_team_names = list(roster_data_full.keys())
        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        # 3. EXTRACTION LOGIC
        if comb:
            comb.seek(0)
            img_c = Image.open(comb)
            w, h = img_c.size
            
            # Find the "DEFENSEMEN" horizontal divider to split the grids
            d_data = pytesseract.image_to_data(img_c, output_type=pytesseract.Output.DICT)
            split_y = int(h * 0.6) # Default fallback (60% down)
            for i, txt in enumerate(d_data['text']):
                if 'DEFENSE' in txt.upper():
                    split_y = d_data['top'][i]
                    print(f"[COMBINED LOG] Found Divider at Y: {split_y}", flush=True)
                    break
            
            # --- FORWARDS SECTION (Top 4x3 Grid) ---
            f_zone = img_c.crop((0, 0, w, split_y))
            f_bins = get_grid_binned_text(f_zone, 4, 3)
            forwards, used = [], set()
            print("\n--- PROCESSING COMBINED FORWARDS ---", flush=True)
            for i, txt in enumerate(f_bins):
                m = match_name_to_roster(txt, all_team_names, f_names, used, roster_data_full)
                forwards.append(m if m else f"PLAYER {i+1}")
                if m: used.add(m)
            
            # --- DEFENSE SECTION (Bottom Left 3x2 Grid) ---
            # Crop bottom part, but only left 2/3rds to avoid the Goalies column
            d_zone = img_c.crop((0, split_y, int(w * 0.68), h))
            d_bins = get_grid_binned_text(d_zone, 3, 2)
            defense, used_d = [], set()
            print("\n--- PROCESSING COMBINED DEFENSE ---", flush=True)
            for i, txt in enumerate(d_bins):
                m = match_name_to_roster(txt, all_team_names, d_names, used_d, roster_data_full)
                defense.append(m if m else f"PLAYER {i+13}")
                if m: used_d.add(m)
        else:
            f_file.seek(0); d_file.seek(0)
            forwards = extract_players_from_image(f_file, 12, f_names, all_team_names, "FORWARDS", roster_data_full)
            defense = extract_players_from_image(d_file, 6, d_names, all_team_names, "DEFENSE", roster_data_full)

        # 4. Final Assembly
        final_f, final_d = [], []
        for n in forwards:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_f.append({'name': n, 'number': info['number'], 'is_forward': True, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})
        for n in defense:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_d.append({'name': n, 'number': info['number'], 'is_forward': False, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})

        return jsonify({
            'forwards': final_f, 'defensemen': final_d,
            'goalies': goalies[:2], 'coaches': get_coaches_from_nhl(team), 'team': team
        })
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    try:
        team = request.form.get('team')
        roster_img = request.files.get('roster_screenshot')
        l_text, l_img = request.form.get('lines_text'), request.files.get('lines_screenshot')
        ref_roster, _ = extract_roster_from_screenshot(roster_img)
        nums = extract_line_numbers(text=l_text, image_file=l_img)
        api = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        f_out, d_out = [], []
        for i, n in enumerate(nums[:18]):
            name = ref_roster.get(n, {}).get('name', f"PLAYER #{n}")
            pid = next((p['id'] for group in ['forwards', 'defensemen'] for p in api.get(group, []) if str(p['sweaterNumber']) == n), None)
            obj = {'name': name, 'number': n, 'is_forward': i < 12, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{pid}.png" if pid else None}
            if i < 12: f_out.append(obj)
            else: d_out.append(obj)
        return jsonify({
            'forwards': f_out, 'defensemen': d_out,
            'goalies': [{'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{p['id']}.png"} for p in api.get('goalies', [])][:2],
            'coaches': get_coaches_from_nhl(team), 'team': team
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
