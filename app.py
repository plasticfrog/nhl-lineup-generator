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

def get_grid_ordered_text(image):
    """
    Groups words into rows using a large vertical threshold (to catch name + number)
    then sorts each row strictly Left-to-Right.
    """
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    words = []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) > 1:
            words.append({
                'text': text,
                'left': data['left'][i],
                'top': data['top'][i],
                'height': data['height'][i],
                'center_y': data['top'][i] + (data['height'][i] / 2)
            })
    
    if not words: return []

    # Sort by Y to begin row clustering
    words.sort(key=lambda x: x['center_y'])
    
    rows = []
    if words:
        current_row = [words[0]]
        # 150px threshold is enough to group names/numbers on a jersey into one 'row'
        for i in range(1, len(words)):
            if abs(words[i]['center_y'] - current_row[0]['center_y']) < 150:
                current_row.append(words[i])
            else:
                current_row.sort(key=lambda x: x['left'])
                rows.append(current_row)
                current_row = [words[i]]
        current_row.sort(key=lambda x: x['left'])
        rows.append(current_row)

    final_lines = []
    for row in rows:
        line_text = " ".join([w['text'] for w in row])
        print(f"[OCR ROW] {line_text}", flush=True)
        final_lines.append(line_text)
    return final_lines

def match_name_to_roster(ocr_text, roster_list, used_names, is_forward_context, roster_data_full):
    """Matches based on Name + Sweater Number to handle duplicates like Pettersson"""
    ocr_text = ocr_text.upper().replace('3', 'S').replace('5', 'S').replace('0', 'O')
    
    # Check for numbers in the OCR string (e.g. #40)
    found_numbers = re.findall(r'\d+', ocr_text)
    
    best_match = None
    best_score = 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        player_info = roster_data_full.get(roster_name, {})
        
        # Priority 1: Match sweater number found in text
        if found_numbers and player_info.get('number') in found_numbers:
            # Check if name also matches partially
            last_name = roster_name.split()[-1]
            if last_name in ocr_text:
                return roster_name # Perfect match

        # Priority 2: Fuzzy name match
        last_name = roster_name.split()[-1]
        if last_name in ocr_text:
            score = 100
            # Context check (Forward vs Defense)
            if is_forward_context == player_info.get('is_forward'):
                score += 50
            
            if score > best_score:
                best_score = score
                best_match = roster_name

    return best_match if best_score >= 100 else None

def extract_players_from_image(image_file, expected_count, team_roster, type_label, is_forward, roster_data_full):
    try:
        print(f"\n--- PROCESSING {type_label} ---", flush=True)
        image = Image.open(image_file)
        lines = get_grid_ordered_text(image)
        matched_names = []
        used_roster = set()
        
        for line in lines:
            # We look for up to 3 players in a single row for forwards, 2 for defense
            row_matches = []
            words = line.split()
            
            # Use sliding window to find players in the row string
            for i in range(len(words)):
                # Try name combinations (Last name usually enough with number)
                potential_chunk = " ".join(words[max(0, i-2):i+1])
                match = match_name_to_roster(potential_chunk, team_roster, used_roster, is_forward, roster_data_full)
                if match and match not in row_matches:
                    row_matches.append(match)
                    used_roster.add(match)
            
            for m in row_matches:
                print(f"[{type_label} FOUND] {m}", flush=True)
                matched_names.append(m)

        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        return matched_names[:expected_count]
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense, roster_goalies, roster_data_full):
    try:
        image = Image.open(image_file)
        lines = get_grid_ordered_text(image)
        extracted = []
        used = set()
        all_skaters = roster_forwards + roster_defense
        
        for line in lines:
            if any(x in line.upper() for x in ['GOALIE', 'COACH']): continue
            words = line.split()
            for i in range(len(words)):
                chunk = " ".join(words[max(0, i-2):i+1])
                # Context is tricky here, so we allow both
                match = match_name_to_roster(chunk, all_skaters, used, None, roster_data_full)
                if match and match not in extracted:
                    extracted.append(match)
                    used.add(match)
                    
        f = extracted[:12]
        d = extracted[12:18]
        while len(f) < 12: f.append(f"PLAYER {len(f)+1}")
        while len(d) < 6: d.append(f"PLAYER {len(d)+1}")
        return f, d
    except:
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

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

# RESTORING NUMBER ENTRY LOGIC
def extract_roster_from_screenshot(image_file):
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image)
        roster = {}
        for line in text.split('\n'):
            nums = re.findall(r'^\d+', line.strip())
            if nums:
                num = nums[0]
                name = line.replace(num, '').strip().upper()
                roster[num] = {'name': name}
        return roster, []
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    if text: return re.findall(r'\d+', text)
    if image_file:
        return re.findall(r'\d+', pytesseract.image_to_string(Image.open(image_file)))
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
        f_file = request.files.get('forwards')
        d_file = request.files.get('defense')
        
        sample = comb if comb else f_file
        sample.seek(0)
        img = Image.open(sample)
        test_lines = get_grid_ordered_text(img)
        
        found_teams = []
        for line in test_lines[:5]:
            for word in line.split():
                res = search_player(word)
                if res and res['team']: found_teams.append(res['team'])
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        print(f"DETECTED TEAM: {team}", flush=True)
        
        roster_api = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        roster_data_full = {}
        goalies = []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in roster_api.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos_key == 'goalies':
                    goalies.append({'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{p['id']}.png"})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos_key=='forwards'}

        f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]

        if comb:
            comb.seek(0)
            forwards, defense = extract_players_from_combined_image(comb, f_names, d_names, [], roster_data_full)
        else:
            f_file.seek(0); d_file.seek(0)
            forwards = extract_players_from_image(f_file, 12, f_names, "FORWARDS", True, roster_data_full)
            defense = extract_players_from_image(d_file, 6, d_names, "DEFENSE", False, roster_data_full)

        all_p = []
        for n in forwards + defense:
            info = roster_data_full.get(n, {'id': None, 'number': ''})
            all_p.append({
                'name': n, 'number': info['number'], 'is_forward': n in forwards,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{info['id']}.png" if info['id'] else None
            })

        return jsonify({
            'forwards': [p for p in all_p if p['is_forward']],
            'defensemen': [p for p in all_p if not p['is_forward']],
            'goalies': goalies[:2],
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
