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
    """Fix common OCR character swaps in names"""
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

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense, roster_goalies):
    """Extracts players and sorts them into buckets based on roster position"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        
        found_forwards = []
        found_defense = []
        found_goalies = []
        used_names = set()
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Skip noise
            if not line or any(x in line.upper() for x in ['OVERALL', 'HOME', 'ROAD', 'IGP:', 'G:', 'A:', 'P:']):
                i += 1
                continue
            
            # Identify name line (Words with at least 2 letters)
            words = [w.upper() for w in line.split() if len(re.sub(r'[^A-Z0-9]', '', w)) >= 2]
            
            # Check for name pair (First names line followed by Last names line)
            if len(words) >= 2 and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                next_words = [w.upper() for w in next_line.split() if len(re.sub(r'[^A-Z0-9]', '', w)) >= 2]
                
                if len(next_words) >= 2:
                    # We found a horizontal row of names
                    for j in range(min(len(words), len(next_words))):
                        full_ocr_name = f"{words[j]} {next_words[j]}"
                        
                        # BUCKET MATCHING: Check which group this name belongs to
                        # 1. Check Goalies
                        g_match = match_name_to_roster(full_ocr_name, roster_goalies, used_names)
                        if g_match:
                            found_goalies.append(g_match)
                            used_names.add(g_match)
                            continue
                        
                        # 2. Check Forwards
                        f_match = match_name_to_roster(full_ocr_name, roster_forwards, used_names)
                        if f_match:
                            found_forwards.append(f_match)
                            used_names.add(f_match)
                            continue
                            
                        # 3. Check Defense
                        d_match = match_name_to_roster(full_ocr_name, roster_defense, used_names)
                        if d_match:
                            found_defense.append(d_match)
                            used_names.add(d_match)
                            continue

                    i += 2
                    continue
            i += 1
            
        return found_forwards, found_defense, found_goalies
        
    except Exception as e:
        print(f"OCR Error: {e}")
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
        
        # 1. Detect Team
        combined_file.seek(0)
        img = Image.open(combined_file)
        text = clean_ocr_text(pytesseract.image_to_string(img, config='--psm 6'))
        found_teams = []
        words = [w for w in text.split() if len(w) > 3]
        for i in range(len(words)-1):
            res = search_player(f"{words[i]} {words[i+1]}")
            if res and res['team']: found_teams.append(res['team'])
            if len(found_teams) > 5: break
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        # 2. Get Full Team Roster
        r_url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        roster_json = requests.get(r_url).json()
        
        roster_data = {}
        r_forwards, r_defense, r_goalies = [], [], []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in roster_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                p_info = {'id': p['id'], 'num': str(p['sweaterNumber']), 'name': name}
                roster_data[name] = p_info
                if pos_key == 'forwards': r_forwards.append(name)
                elif pos_key == 'defensemen': r_defense.append(name)
                else: r_goalies.append(name)

        # 3. Extract and bucket players
        combined_file.seek(0)
        f_found, d_found, g_found = extract_players_from_combined_image(combined_file, r_forwards, r_defense, r_goalies)
        
        # 4. Build Final Lists
        def build_player_obj(name_list, is_f):
            results = []
            for name in name_list:
                info = roster_data.get(name, {'id': None, 'num': ''})
                results.append({
                    'name': name, 'number': info['num'], 
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{info['id']}.png" if info['id'] else None
                })
            return results

        final_forwards = build_player_obj(f_found[:12], True)
        final_defense = build_player_obj(d_found[:6], False)
        
        # Pad if OCR missed anyone
        while len(final_forwards) < 12: final_forwards.append({'name': 'EMPTY', 'number': '', 'headshot_url': None})
        while len(final_defense) < 6: final_defense.append({'name': 'EMPTY', 'number': '', 'headshot_url': None})

        # Process Goalies
        final_goalies = []
        for g_name in (g_found + r_goalies)[:2]: # Use found goalies first, then roster defaults
            info = roster_data.get(g_name)
            final_goalies.append({
                'name': f"#{info['num']} {g_name.split()[-1]}",
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{info['id']}.png"
            })

        return jsonify({
            'forwards': final_forwards,
            'defensemen': final_defense,
            'goalies': final_goalies,
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
