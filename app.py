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

def match_player(ocr_text, roster_dict, used_ids):
    """Matches by Number first, then fuzzy name matching"""
    clean_text = clean_ocr_text(ocr_text.upper())
    numbers_found = re.findall(r'\d+', clean_text)
    
    # 1. Match by Number (Highly accurate for jersey graphics)
    if numbers_found:
        for num in numbers_found:
            for name, data in roster_dict.items():
                if data['num'] == num and data['id'] not in used_ids:
                    return name

    # 2. Fallback to Name matching
    best_match = None
    best_score = 0
    ocr_parts = re.sub(r'[^A-Z\s]', '', clean_text).split()
    
    for name, data in roster_dict.items():
        if data['id'] in used_ids: continue
        score = 0
        roster_parts = name.split()
        if ocr_parts and roster_parts:
            if ocr_parts[-1] == roster_parts[-1]: score += 100
            elif ocr_parts[-1] in roster_parts[-1]: score += 80
        if score > best_score:
            best_score = score
            best_match = name
            
    return best_match if best_score >= 70 else None

def extract_in_order(image_file, roster_dict, expected_count):
    """Extracts players line-by-line to preserve grid order"""
    try:
        image = Image.open(image_file)
        # PSM 11 is great for jerseys, PSM 6 is a backup for text blocks
        text = pytesseract.image_to_string(image, config='--psm 11')
        lines = text.split('\n')
        
        found_players = []
        used_ids = set()
        
        for line in lines:
            line = line.strip()
            if len(line) < 2: continue
            
            # Split line into chunks (handling the gaps between jerseys)
            parts = re.split(r'\s{2,}', line)
            for part in parts:
                if len(part) < 2: continue
                match = match_player(part, roster_dict, used_ids)
                if match:
                    found_players.append(match)
                    used_ids.add(roster_dict[match]['id'])
                else:
                    # If we found a name but it's not on roster (like Toews), 
                    # keep the raw text so the user can see it
                    clean_name = re.sub(r'[^A-Z\s]', '', clean_ocr_text(part)).strip()
                    if len(clean_name) > 4 and clean_name not in ["GP", "GAA"]:
                        found_players.append(clean_name)
                
                if len(found_players) >= expected_count:
                    return found_players
        return found_players
    except Exception as e:
        print(f"Extraction Error: {e}")
        return []

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
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        # Detect Team
        sample = combined_file if combined_file else forwards_file
        sample.seek(0)
        text = clean_ocr_text(pytesseract.image_to_string(Image.open(sample), config='--psm 6'))
        words = [w for w in text.split() if len(w) > 3]
        found_teams = []
        for i in range(len(words)-1):
            search_name = f"{words[i]}%20{words[i+1]}".lower()
            try:
                res = requests.get(f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=1&q={search_name}", timeout=5).json()
                if res: found_teams.append(res[0].get('teamAbbrev'))
            except: pass
            if len(found_teams) > 2: break
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'WPG'
        
        # Get Official Roster
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        roster_data = {}
        r_goalies = []
        
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                info = {'id': p['id'], 'num': str(p['sweaterNumber']), 'name': name}
                roster_data[name] = info
                if pos_key == 'goalies': r_goalies.append(name)

        # --- EXTRACTION ---
        final_forwards = []
        final_defense = []

        if combined_file:
            combined_file.seek(0)
            all_found = extract_in_order(combined_file, roster_data, 18)
            final_forwards = all_found[:12]
            final_defense = all_found[12:18]
        else:
            if forwards_file:
                forwards_file.seek(0)
                final_forwards = extract_in_order(forwards_file, roster_data, 12)
            if defense_file:
                defense_file.seek(0)
                final_defense = extract_in_order(defense_file, roster_data, 6)

        def build_output(names, count):
            res = []
            for name in names:
                info = roster_data.get(name)
                if info:
                    res.append({
                        'name': name, 
                        'number': info['num'], 
                        'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{team}/{info['id']}.png"
                    })
                else:
                    # SAFETY: Player not on official roster (retired/custom)
                    res.append({'name': name, 'number': '', 'headshot_url': None})
            
            while len(res) < count:
                res.append({'name': 'EMPTY', 'number': '', 'headshot_url': None})
            return res

        g_output = []
        for g_name in r_goalies[:2]:
            info = roster_data[g_name]
            g_output.append({
                'name': f"#{info['num']} {g_name.split()[-1]}", 
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/latest/{team}/{info['id']}.png"
            })

        return jsonify({
            'forwards': build_output(final_forwards, 12),
            'defensemen': build_output(final_defense, 6),
            'goalies': g_output,
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        print(f"Process Error: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
