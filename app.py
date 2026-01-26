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

def get_grid_binned_text(image, rows, cols, debug_label=""):
    """
    Mathematical Grid Sorting: 
    Divides the provided image area into a strict R x C grid.
    """
    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    
    bins = [[] for _ in range(rows * cols)]
    cell_w = width / cols
    cell_h = height / rows

    print(f"\n[GRID DEBUG {debug_label}] Partitioning {rows}x{cols}", flush=True)

    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) >= 1:
            # Word center
            cx = data['left'][i] + (data['width'][i] / 2)
            cy = data['top'][i] + (data['height'][i] / 2)
            
            col_idx = int(cx // cell_w)
            row_idx = int(cy // cell_h)
            
            col_idx = max(0, min(col_idx, cols - 1))
            row_idx = max(0, min(row_idx, rows - 1))
            
            bin_idx = (row_idx * cols) + col_idx
            bins[bin_idx].append(text)

    return [" ".join(b) for b in bins]

def match_name_to_roster(ocr_text, roster_list, used_names, roster_data_full):
    """
    STRICT Matcher: Requires the Last Name to be present. 
    Prevents hallucinations based on stray numbers (like #10 Ty Dellandrea).
    """
    if not ocr_text: return None
    clean_text = ocr_text.upper().replace('3', 'S').replace('5', 'S').replace('0', 'O').replace('1', 'I')
    found_nums = re.findall(r'\d+', clean_text)
    
    best_match, best_score = None, 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        last_name = roster_name.split()[-1]
        first_name = roster_name.split()[0]
        
        # CRITICAL: If the last name isn't in the text, we do NOT match.
        # This stops the app from matching "Age: 23" to a player who is #23.
        if last_name not in clean_text:
            continue

        score = 100 # Base score for last name match
        
        # Bonus for first name
        if first_name in clean_text:
            score += 100
        # Bonus for correct number
        if p_info.get('number') in found_nums:
            score += 50

        if score > best_score:
            best_score = score
            best_match = roster_name

    return best_match if best_score >= 100 else None

def extract_players_from_image(image_file, expected_count, preferred_roster, full_roster, type_label, roster_data_full):
    """USED BY DUAL SCREENSHOT METHOD"""
    try:
        print(f"\n=== PROCESSING SEPARATE {type_label} IMAGE ===", flush=True)
        image = Image.open(image_file)
        rows, cols = (4, 3) if expected_count == 12 else (3, 2)
        bins = get_grid_binned_text(image, rows, cols, type_label)
        matched_names, used = [], set()
        for i, bin_text in enumerate(bins):
            match = match_name_to_roster(bin_text, preferred_roster, used, roster_data_full)
            matched_names.append(match if match else f"PLAYER {i+1}")
            if match: used.add(match)
        return matched_names[:expected_count]
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

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
        sample.seek(0)
        img_full = Image.open(sample)
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

        all_team_names = list(roster_data_full.keys())
        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        if comb:
            comb.seek(0)
            img_c = Image.open(comb)
            w, h = img_c.size
            
            # Find the "DEFENSEMEN" horizontal divider to split the grids
            d_ocr = pytesseract.image_to_data(img_c, output_type=pytesseract.Output.DICT)
            split_y = int(h * 0.58) # Conservative fallback
            for i, txt in enumerate(d_ocr['text']):
                if 'DEFENSE' in txt.upper():
                    split_y = d_ocr['top'][i]
                    break

            # 1. Process FORWARDS (Top zone, 4x3 Grid)
            f_zone = img_c.crop((0, 0, w, split_y))
            f_bins = get_grid_binned_text(f_zone, 4, 3, "COMBINED-FORWARDS")
            forwards, used_f = [], set()
            for i, txt in enumerate(f_bins):
                m = match_name_to_roster(txt, f_names, used_f, roster_data_full)
                forwards.append(m if m else f"PLAYER {i+1}")
                if m: used_f.add(m)

            # 2. Process DEFENSE (Bottom zone, left 66%, 3x2 Grid)
            d_zone = img_c.crop((0, split_y, int(w * 0.68), h))
            d_bins = get_grid_binned_text(d_zone, 3, 2, "COMBINED-DEFENSE")
            defense, used_d = [], set()
            for i, txt in enumerate(d_bins):
                m = match_name_to_roster(txt, d_names, used_d, roster_data_full)
                defense.append(m if m else f"PLAYER {i+13}")
                if m: used_d.add(m)
                
            forwards_raw, defense_raw = forwards, defense
        else:
            f_file.seek(0); d_file.seek(0)
            forwards_raw = extract_players_from_image(f_file, 12, f_names, all_team_names, "FORWARDS", roster_data_full)
            defense_raw = extract_players_from_image(d_file, 6, d_names, all_team_names, "DEFENSE", roster_data_full)

        final_f, final_d = [], []
        for n in forwards_raw:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_f.append({'name': n, 'number': info['number'], 'is_forward': True, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})
        for n in defense_raw:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_d.append({'name': n, 'number': info['number'], 'is_forward': False, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{info['id']}.png" if info['id'] else None})

        return jsonify({
            'forwards': final_f, 'defensemen': final_d,
            'goalies': [{'name': g['name'], 'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{g['id']}.png"} for g in goalies[:2]],
            'coaches': get_coaches_from_nhl(team), 'team': team
        })
    except Exception as e:
        print(f"Error: {e}", flush=True); return jsonify({'error': str(e)}), 500

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
    except Exception as e: return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
