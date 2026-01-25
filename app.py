from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
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

def clean_ocr_text(text):
    """Fix common OCR character swaps in names"""
    return text.replace('3', 'S').replace('5', 'S').replace('0', 'O').replace('1', 'I').replace('4', 'A').replace('8', 'B')

def match_by_name(ocr_text, roster_list, used_names):
    """Fuzzy match names based on roster list provided"""
    ocr_name = clean_ocr_text(ocr_text.upper())
    ocr_name_clean = re.sub(r'[^A-Z\s]', '', ocr_name).strip()
    ocr_parts = ocr_name_clean.split()
    
    if not ocr_parts: return None
    
    best_match = None
    best_score = 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        roster_parts = roster_name.split()
        score = 0
        
        # Match Last Name
        if ocr_parts[-1] == roster_parts[-1]:
            score += 100
        elif ocr_parts[-1] in roster_parts[-1] or roster_parts[-1] in ocr_parts[-1]:
            score += 80
            
        # Match First Name
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            if ocr_parts[0] == roster_parts[0]:
                score += 50
        
        if score > best_score:
            best_score = score
            best_match = roster_name
            
    return best_match if best_score >= 70 else None

def extract_players(image_file, roster_subset, expected_count):
    """Extracts names in grid order from separate images"""
    try:
        image = Image.open(image_file)
        # Using PSM 11 (Sparse Text) for jersey graphics
        text = pytesseract.image_to_string(image, config='--psm 11')
        lines = text.split('\n')
        
        found_players = []
        used_names = set()
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            chunks = re.split(r'\s{2,}', line)
            for chunk in chunks:
                if len(chunk) < 3: continue
                match = match_by_name(chunk, roster_subset, used_names)
                if match:
                    found_players.append(match)
                    used_names.add(match)
                else:
                    raw_name = re.sub(r'[^A-Z\s]', '', clean_ocr_text(chunk)).strip()
                    if len(raw_name) > 4 and raw_name not in ["GP", "GAA", "PTS", "IGP"]:
                        found_players.append(raw_name)
                
                if len(found_players) >= expected_count:
                    return found_players
        return found_players
    except:
        return []

def get_coaches_from_nhl(team_abbrev):
    coaches = []
    try:
        slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{slug}/team/coaches"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.ok:
            soup = BeautifulSoup(response.text, 'html.parser')
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
        
        # Detect Team Safely
        sample = combined_file if combined_file else forwards_file
        sample.seek(0)
        text = clean_ocr_text(pytesseract.image_to_string(Image.open(sample), config='--psm 6'))
        words = [w for w in text.split() if len(w) > 3]
        found_teams = []
        for i in range(len(words)-1):
            search_name = f"{words[i]}%20{words[i+1]}".lower()
            try:
                res = requests.get(f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=1&q={search_name}", timeout=5)
                if res.ok and len(res.json()) > 0:
                    found_teams.append(res.json()[0].get('teamAbbrev'))
            except: pass
            if len(found_teams) > 1: break
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'WPG'
        
        # Get Official Roster
        roster_response = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current")
        if not roster_response.ok:
            return jsonify({'error': 'Could not fetch NHL roster. Please try again.'}), 500
        
        r_json = roster_response.json()
        roster_data = {}
        r_forwards, r_defense, r_goalies = [], [], []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                roster_data[name] = {'id': p['id'], 'num': str(p['sweaterNumber'])}
                if pos_key == 'forwards': r_forwards.append(name)
                elif pos_key == 'defensemen': r_defense.append(name)
                else: r_goalies.append(name)

        # Extraction with Position Protection
        final_forwards_names = []
        final_defense_names = []

        if combined_file:
            combined_file.seek(0)
            all_names = extract_players(combined_file, r_forwards + r_defense, 18)
            final_forwards_names = all_names[:12]
            final_defense_names = all_names[12:18]
        else:
            if forwards_file:
                forwards_file.seek(0)
                final_forwards_names = extract_players(forwards_file, r_forwards, 12)
            if defense_file:
                defense_file.seek(0)
                final_defense_names = extract_players(defense_file, r_defense, 6)

        def build_output(names, count):
            res = []
            for name in names:
                info = roster_data.get(name)
                if info:
                    res.append({
                        'name': name, 'number': info['num'], 
                        'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{info['id']}.png"
                    })
                else:
                    res.append({'name': name, 'number': '', 'headshot_url': None})
            while len(res) < count:
                res.append({'name': 'EMPTY', 'number': '', 'headshot_url': None})
            return res

        g_output = []
        for g_name in r_goalies[:2]:
            info = roster_data[g_name]
            g_output.append({
                'name': f"#{info['num']} {g_name.split()[-1]}", 
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{info['id']}.png"
            })

        return jsonify({
            'forwards': build_output(final_forwards_names, 12),
            'defensemen': build_output(final_defense_names, 6),
            'goalies': g_output,
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        return jsonify({'error': f'System Error: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
