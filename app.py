from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
import time
import re
from collections import Counter
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = '/tmp/nhl_uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ALL ORIGINAL TEAM MAPPINGS RESTORED
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
    Divides the image into a perfect grid and assigns OCR text to specific slots.
    Ensures Left-to-Right and Top-to-Bottom order is 100% preserved.
    """
    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    
    # Initialize bins
    bins = [[] for _ in range(rows * cols)]
    
    cell_w = width / cols
    cell_h = height / rows

    print(f"\n[GRID DEBUG] Processing {rows}x{cols} Grid ({width}x{height})", flush=True)

    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) >= 1:
            x = data['left'][i] + (data['width'][i] / 2)
            y = data['top'][i] + (data['height'][i] / 2)
            
            # Determine which cell this word belongs to
            col_idx = int(x // cell_w)
            row_idx = int(y // cell_h)
            
            # Constrain to grid boundaries
            col_idx = max(0, min(col_idx, cols - 1))
            row_idx = max(0, min(row_idx, rows - 1))
            
            bin_idx = (row_idx * cols) + col_idx
            bins[bin_idx].append(text)
            print(f"  Word '{text}' mapped to Slot {bin_idx+1} (Row:{row_idx}, Col:{col_idx})", flush=True)

    # Join text in each bin
    return [" ".join(b) for b in bins]

def match_name_to_roster(ocr_text, roster_list, used_names, is_forward_context, roster_data_full):
    """Matches based on Name + Sweater Number to handle duplicates like Pettersson"""
    if not ocr_text: return None
    
    clean_ocr = ocr_text.upper().replace('3', 'S').replace('5', 'S').replace('0', 'O')
    found_nums = re.findall(r'\d+', clean_ocr)
    
    best_match = None
    best_score = 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        
        # Priority 1: Exact Number Match (Best for Pettersson #40 vs #29)
        if found_nums and p_info.get('number') in found_nums:
            last_name = roster_name.split()[-1]
            if last_name in clean_ocr:
                return roster_name 

        # Priority 2: Name + Position Match
        last_name = roster_name.split()[-1]
        if last_name in clean_ocr:
            score = 100
            if is_forward_context is not None and p_info.get('is_forward') == is_forward_context:
                score += 50
            if score > best_score:
                best_score = score
                best_match = roster_name

    return best_match if best_score >= 100 else None

def extract_players_from_image(image_file, expected_count, team_roster, type_label, is_forward, roster_data_full):
    try:
        print(f"\n=== EXTRACTING {type_label} ===", flush=True)
        image = Image.open(image_file)
        
        # Set Grid Dimensions
        if expected_count == 12: rows, cols = 4, 3
        elif expected_count == 6: rows, cols = 3, 2
        else: rows, cols = expected_count, 1 # Fallback
            
        slot_texts = get_grid_binned_text(image, rows, cols)
        matched_names = []
        used_roster = set()
        
        for i, text in enumerate(slot_texts):
            match = match_name_to_roster(text, team_roster, used_roster, is_forward, roster_data_full)
            if match:
                print(f"  [SLOT {i+1}] Matched: {match}", flush=True)
                matched_names.append(match)
                used_roster.add(match)
            else:
                print(f"  [SLOT {i+1}] No match found in text: '{text}'", flush=True)
                matched_names.append(f"PLAYER {i+1}")
                
        return matched_names[:expected_count]
    except Exception as e:
        print(f"[ERROR] {type_label} failed: {e}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

# ALL ORIGINAL SECONDARY FUNCTIONS RESTORED
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

# ALL ORIGINAL ROUTES RESTORED
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
        f_file = request.files.get('forwards')
        d_file = request.files.get('defense')
        
        sample = comb if comb else f_file
        sample.seek(0)
        img = Image.open(sample)
        # Use a generic grid to find team names
        test_blocks = get_grid_binned_text(img, 4, 3)
        found_teams = []
        for b in test_blocks:
            res = search_player(b)
            if res and res['team']: found_teams.append(res['team'])
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        print(f"[PROCESS] Detected Team: {team}", flush=True)
        
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        roster_data_full = {}
        goalies = []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos_key == 'goalies':
                    goalies.append({'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{p['id']}.png"})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos_key=='forwards'}

        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        if comb:
            comb.seek(0)
            img_c = Image.open(comb)
            # 6 rows, 3 columns for 18 skaters
            all_slots = get_grid_binned_text(img_c, 6, 3)
            all_players = []
            used = set()
            for i, txt in enumerate(all_slots):
                # First 12 are forwards, next 6 are defense
                ctx = True if i < 12 else False
                r_list = f_names if i < 12 else d_names
                m = match_name_to_roster(txt, r_list, used, ctx, roster_data_full)
                all_players.append(m if m else f"PLAYER {i+1}")
                if m: used.add(m)
            forwards = all_players[:12]
            defense = all_players[12:18]
        else:
            f_file.seek(0); d_file.seek(0)
            forwards = extract_players_from_image(f_file, 12, f_names, "FORWARDS", True, roster_data_full)
            defense = extract_players_from_image(d_file, 6, d_names, "DEFENSE", False, roster_data_full)

        final_players = []
        for n in forwards + defense:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            final_players.append({
                'name': n, 'number': info['number'], 'is_forward': n in forwards,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{info['id']}.png" if info['id'] else None
            })

        return jsonify({
            'forwards': [p for p in final_players if p['is_forward']],
            'defensemen': [p for p in final_players if not p['is_forward']],
            'goalies': goalies[:2],
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        print(f"[CRITICAL] Failure: {e}", flush=True)
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    try:
        team = request.form.get('team')
        roster_img = request.files.get('roster_screenshot')
        l_text = request.form.get('lines_text')
        l_img = request.files.get('lines_screenshot')
        
        ref_roster, _ = extract_roster_from_screenshot(roster_img)
        nums = extract_line_numbers(text=l_text, image_file=l_img)
        
        api = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        final = []
        for i, n in enumerate(nums[:18]):
            name = ref_roster.get(n, {}).get('name', f"PLAYER #{n}")
            pid = next((p['id'] for group in ['forwards', 'defensemen'] for p in api.get(group, []) if str(p['sweaterNumber']) == n), None)
            final.append({
                'name': name, 'number': n, 'is_forward': i < 12,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{pid}.png" if pid else None
            })
        
        return jsonify({
            'forwards': [p for p in final if p['is_forward']],
            'defensemen': [p for p in final if not p['is_forward']],
            'goalies': [{'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{p['id']}.png"} for p in api.get('goalies', [])][:2],
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
