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

# Team name mapping for NHL.com URLs
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

def clean_ocr_text(text):
    """Fix common OCR character swaps in names (3AM -> SAM, etc)"""
    return text.replace('3', 'S').replace('5', 'S').replace('0', 'O').replace('1', 'I').replace('4', 'A').replace('8', 'B')

def match_name_to_roster(ocr_name, roster_list, used_names):
    """Find best matching name from roster using fuzzy matching"""
    ocr_name = clean_ocr_text(ocr_name.upper())
    best_match = None
    best_score = 0
    
    ocr_parts = ocr_name.split()
    if not ocr_parts: return None
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        
        roster_parts = roster_name.split()
        score = 0
        
        if len(ocr_parts) > 0 and len(roster_parts) > 0:
            ocr_last = ocr_parts[-1]
            roster_last = roster_parts[-1]
            if ocr_last == roster_last: score += 100
            elif ocr_last in roster_last or roster_last in ocr_last: score += 80
        
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            if ocr_parts[0] == roster_parts[0]: score += 50
            
        if score > best_score:
            best_score = score
            best_match = roster_name
            
    return best_match if best_score >= 50 else None

def extract_from_any_image(image_file, r_forwards, r_defense, r_goalies):
    """Extract names and sort them into Forward/Defense/Goalie buckets"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        
        found_f, found_d, found_g = [], [], []
        used = set()
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or any(x in line.upper() for x in ['OVERALL', 'HOME', 'ROAD', 'IGP:', 'G:', 'A:', 'P:', 'H:', 'W:', 'ACE:']):
                i += 1
                continue
            
            words = [w.upper() for w in line.split() if len(re.sub(r'[^A-Z0-9]', '', w)) >= 2]
            if len(words) >= 2 and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                next_words = [w.upper() for w in next_line.split() if len(re.sub(r'[^A-Z0-9]', '', w)) >= 2]
                
                if len(next_words) >= 2:
                    for j in range(min(len(words), len(next_words))):
                        full_name = f"{words[j]} {next_words[j]}"
                        
                        # Bucket Matching
                        g_match = match_name_to_roster(full_name, r_goalies, used)
                        if g_match:
                            found_g.append(g_match); used.add(g_match)
                            continue
                        
                        f_match = match_name_to_roster(full_name, r_forwards, used)
                        if f_match:
                            found_f.append(f_match); used.add(f_match)
                            continue
                            
                        d_match = match_name_to_roster(full_name, r_defense, used)
                        if d_match:
                            found_d.append(d_match); used.add(d_match)
                            continue
                    i += 2
                    continue
            i += 1
        return found_f, found_d, found_g
    except:
        return [], [], []

def search_player(player_name):
    try:
        search_name = player_name.lower().replace(' ', '%20')
        url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={search_name}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
        if res: return {'id': res[0]['playerId'], 'team': res[0].get('teamAbbrev')}
    except: pass
    return None

def get_coaches_from_nhl(team_abbrev):
    # This is a basic version - your index.html handles the Sharks special list
    coaches = []
    try:
        slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{slug}/team/coaches"
        soup = BeautifulSoup(requests.get(url, timeout=10).text, 'html.parser')
        for img in soup.find_all('img'):
            alt = img.get('alt', '').lower()
            if 'coach' in alt:
                name = img.get('alt', '').split('-')[0].strip().upper()
                role = 'HEAD COACH' if 'head' in alt else 'ASSISTANT COACH'
                coaches.append({'name': name, 'role': role, 'headshot_url': img.get('src')})
                if len(coaches) >= 4: break
    except: pass
    return coaches

@app.route('/')
def index(): return render_template('index.html')

@app.route('/numbers')
def numbers_page(): return render_template('index_numbers.html')

@app.route('/lineup')
def lineup(): return render_template('lineup.html')

@app.route('/process', methods=['POST'])
def process_lineup():
    try:
        combined_file = request.files.get('combined')
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        # 1. Detect Team using whatever file is available
        sample_file = combined_file if combined_file else forwards_file
        if not sample_file:
            return jsonify({'error': 'No image uploaded'}), 400
            
        sample_file.seek(0)
        img = Image.open(sample_file)
        text = clean_ocr_text(pytesseract.image_to_string(img, config='--psm 6'))
        found_teams = []
        words = [w for w in text.split() if len(w) > 3]
        for i in range(len(words)-1):
            res = search_player(f"{words[i]} {words[i+1]}")
            if res and res['team']: found_teams.append(res['team'])
            if len(found_teams) > 3: break
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        # 2. Get Full Team Roster
        r_url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        roster_json = requests.get(r_url).json()
        
        roster_data = {}
        r_forwards, r_defense, r_goalies = [], [], []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in roster_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                roster_data[name] = {'id': p['id'], 'num': str(p['sweaterNumber']), 'name': name}
                if pos_key == 'forwards': r_forwards.append(name)
                elif pos_key == 'defensemen': r_defense.append(name)
                else: r_goalies.append(name)

        # 3. Extract and bucket players
        f_found, d_found, g_found = [], [], []
        if combined_file:
            combined_file.seek(0)
            f_found, d_found, g_found = extract_from_any_image(combined_file, r_forwards, r_defense, r_goalies)
        else:
            forwards_file.seek(0)
            f1, d1, g1 = extract_from_any_image(forwards_file, r_forwards, r_defense, r_goalies)
            defense_file.seek(0)
            f2, d2, g2 = extract_from_any_image(defense_file, r_forwards, r_defense, r_goalies)
            f_found, d_found, g_found = f1+f2, d1+d2, g1+g2

        # 4. Build Final Data
        def build_list(names, count, is_f):
            results = []
            for name in names[:count]:
                info = roster_data.get(name, {'id': None, 'num': ''})
                results.append({
                    'name': name, 'number': info['num'], 
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{info['id']}.png" if info['id'] else None
                })
            while len(results) < count:
                results.append({'name': 'EMPTY', 'number': '', 'headshot_url': None})
            return results

        final_goalies = []
        for g_name in (g_found + r_goalies)[:2]:
            info = roster_data.get(g_name)
            final_goalies.append({'name': f"#{info['num']} {g_name.split()[-1]}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{info['id']}.png"})

        return jsonify({
            'forwards': build_list(f_found, 12, True),
            'defensemen': build_list(d_found, 6, False),
            'goalies': final_goalies,
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    try:
        team = request.form.get('team')
        roster_screenshot = request.files.get('roster_screenshot')
        lines_text = request.form.get('lines_text')
        lines_screenshot = request.files.get('lines_screenshot')
        
        # Simple Number Entry logic
        from app import extract_roster_from_screenshot, extract_line_numbers
        roster, _ = extract_roster_from_screenshot(roster_screenshot)
        nums = extract_line_numbers(text=lines_text, image_file=lines_screenshot)
        
        api_roster = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        final_players = []
        for i, n in enumerate(nums[:18]):
            name = roster.get(n, {}).get('name', f"PLAYER #{n}")
            pid = next((p['id'] for group in ['forwards', 'defensemen'] for p in api_roster.get(group, []) if str(p['sweaterNumber']) == n), None)
            final_players.append({'name': name, 'number': n, 'is_forward': i < 12, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{pid}.png" if pid else None})

        return jsonify({
            'forwards': [p for p in final_players if p['is_forward']],
            'defensemen': [p for p in final_players if not p['is_forward']],
            'goalies': [], # Simplified for numbers
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def extract_roster_from_screenshot(image_file):
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        roster = {}
        for line in text.split('\n'):
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit():
                roster[parts[0]] = {'name': ' '.join(parts[1:]).upper()}
        return roster, []
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    if text: return re.findall(r'\d+', text)
    if image_file:
        try: return re.findall(r'\d+', pytesseract.image_to_string(Image.open(image_file)))
        except: return []
    return []

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
