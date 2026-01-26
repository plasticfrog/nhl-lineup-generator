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

# FULL TEAM NAME MAPPING PRESERVED
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

def get_grid_sorted_jerseys(image):
    """
    STRICT GRID SORTING:
    1. Extracts all words with (X, Y) coordinates.
    2. Clusters words into 'Jerseys' (grouping name + number).
    3. Sorts jerseys Top-to-Bottom (Rows), then Left-to-Right (Columns).
    """
    print("\n[GRID DEBUG] Starting Spatial Analysis...", flush=True)
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    words = []
    
    # Extract raw data
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) >= 1:
            w = {
                'text': text,
                'x': data['left'][i],
                'y': data['top'][i],
                'w': data['width'][i],
                'h': data['height'][i],
                'cx': data['left'][i] + (data['width'][i] / 2),
                'cy': data['top'][i] + (data['height'][i] / 2)
            }
            words.append(w)
            # Log every word found for terminal debugging
            print(f"  Word Found: '{text}' at [X:{w['x']}, Y:{w['y']}]", flush=True)

    if not words: return []

    # Cluster words into Jerseys based on proximity
    # Words are part of the same jersey if they are vertically close or horizontally very close
    jerseys = []
    used_indices = set()
    
    for i in range(len(words)):
        if i in used_indices: continue
        cluster = [words[i]]
        used_indices.add(i)
        
        # Look for words belonging to this same jersey
        for j in range(len(words)):
            if j in used_indices: continue
            # If word J is vertically close (name vs number) and horizontally aligned
            # or horizontally very close (first name vs last name)
            dist_y = abs(words[i]['cy'] - words[j]['cy'])
            dist_x = abs(words[i]['cx'] - words[j]['cx'])
            
            # Thresholds: within 120px vertical or 200px horizontal is usually one jersey area
            if (dist_y < 120 and dist_x < 150) or (dist_y < 40 and dist_x < 300):
                cluster.append(words[j])
                used_indices.add(j)
        
        # Create a Jersey Object
        jersey_text = " ".join([w['text'] for w in cluster])
        avg_x = sum(w['cx'] for w in cluster) / len(cluster)
        avg_y = sum(w['cy'] for w in cluster) / len(cluster)
        jerseys.append({'text': jersey_text, 'x': avg_x, 'y': avg_y})

    # SORTING LOGIC:
    # 1. Group into Rows using Y (with a 150px tolerance for 'jagged' lines)
    jerseys.sort(key=lambda j: j['y'])
    rows = []
    if jerseys:
        current_row = [jerseys[0]]
        for i in range(1, len(jerseys)):
            if abs(jerseys[i]['y'] - current_row[0]['y']) < 150:
                current_row.append(jerseys[i])
            else:
                current_row.sort(key=lambda j: j['x']) # Sort Left-to-Right in row
                rows.append(current_row)
                current_row = [jerseys[i]]
        current_row.sort(key=lambda j: j['x'])
        rows.append(current_row)

    # Flatten the grid back into a list
    final_order = []
    for r_idx, row in enumerate(rows):
        row_text = " | ".join([j['text'] for j in row])
        print(f"[GRID DEBUG] Row {r_idx+1} Identified: {row_text}", flush=True)
        for j in row:
            final_order.append(j['text'])

    return final_order

def match_name_to_roster(ocr_text, roster_list, used_names, is_forward_context, roster_data_full):
    """
    Fixes the Pettersson duplicate issue by checking:
    1. Sweater Number match (Priority 1)
    2. Name match + Position context (Priority 2)
    """
    clean_ocr = ocr_text.upper().replace('3', 'S').replace('5', 'S').replace('0', 'O')
    found_nums = re.findall(r'\d+', clean_ocr)
    
    best_match = None
    best_score = 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        p_info = roster_data_full.get(roster_name, {})
        
        # PRIORITY 1: Sweater Number Match
        if found_nums and p_info.get('number') in found_nums:
            # If the name also appears, this is a 100% lock
            if roster_name.split()[-1] in clean_ocr:
                return roster_name

        # PRIORITY 2: Name + Position match
        last_name = roster_name.split()[-1]
        if last_name in clean_ocr:
            score = 100
            # Boost if position context matches (e.g. forward found in forward image)
            if is_forward_context is not None and p_info.get('is_forward') == is_forward_context:
                score += 50
            
            if score > best_score:
                best_score = score
                best_match = roster_name
                
    return best_match if best_score >= 100 else None

def extract_players_from_image(image_file, expected_count, team_roster, type_label, is_forward, roster_data_full):
    """Orchestrates image processing and roster matching"""
    try:
        print(f"\n--- PROCESSING {type_label} (EXPECTING {expected_count}) ---", flush=True)
        image = Image.open(image_file)
        ordered_texts = get_grid_sorted_jerseys(image)
        
        matched_names = []
        used_roster = set()
        
        for text_block in ordered_texts:
            match = match_name_to_roster(text_block, team_roster, used_roster, is_forward, roster_data_full)
            if match:
                print(f"  [SUCCESS] Slot {len(matched_names)+1}: {match}", flush=True)
                matched_names.append(match)
                used_roster.add(match)
            else:
                print(f"  [FAILED] No match for block: '{text_block}'", flush=True)
            
            if len(matched_names) >= expected_count: break
            
        # Fill empty slots
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
            
        return matched_names[:expected_count]
    except Exception as e:
        print(f"[CRITICAL ERROR] {type_label} failure: {e}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

# PRESERVED: SEARCH AND SCRAPE LOGIC
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

# PRESERVED: NUMBER ENTRY METHODS
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

# PRESERVED: ROUTES
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
        
        # Detect Team
        sample = comb if comb else f_file
        sample.seek(0)
        img = Image.open(sample)
        test_blocks = get_grid_sorted_jerseys(img)
        found_teams = []
        for b in test_blocks[:10]:
            res = search_player(b)
            if res and res['team']: found_teams.append(res['team'])
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        print(f"[PROCESS] Final Team Detection: {team}", flush=True)
        
        # Fetch Current Roster
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

        # Run Extraction
        if comb:
            comb.seek(0)
            # Combined logic uses the same grid sorter then splits by count
            all_sorted = get_grid_sorted_jerseys(Image.open(comb))
            forwards_raw = all_sorted[:12]
            defense_raw = all_sorted[12:18]
            
            forwards = []
            used = set()
            for b in forwards_raw:
                m = match_name_to_roster(b, f_names, used, True, roster_data_full)
                forwards.append(m if m else f"PLAYER {len(forwards)+1}")
                if m: used.add(m)
                
            defense = []
            used_d = set()
            for b in defense_raw:
                m = match_name_to_roster(b, d_names, used_d, False, roster_data_full)
                defense.append(m if m else f"PLAYER {len(defense)+1}")
                if m: used_d.add(m)
        else:
            f_file.seek(0); d_file.seek(0)
            forwards = extract_players_from_image(f_file, 12, f_names, "FORWARDS", True, roster_data_full)
            defense = extract_players_from_image(d_file, 6, d_names, "DEFENSE", False, roster_data_full)

        # Assemble Final Data
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
        print(f"[CRITICAL] Process failure: {e}", flush=True)
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
