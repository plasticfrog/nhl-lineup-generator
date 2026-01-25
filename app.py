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

def clean_ocr_text(text):
    return text.replace('3', 'S').replace('5', 'S').replace('0', 'O').replace('1', 'I').replace('4', 'A').replace('8', 'B')

def match_name_to_roster(ocr_name, roster_list, used_names):
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
            if ocr_parts[-1] == roster_parts[-1]: score += 100
            elif ocr_parts[-1] in roster_parts[-1]: score += 80
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            if ocr_parts[0] == roster_parts[0]: score += 50
        if score > best_score:
            best_score = score
            best_match = roster_name
    return best_match if best_score >= 50 else None

def extract_players_from_image(image_file, expected_count, team_roster):
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        matched_names = []
        used_roster = set()
        for line in lines:
            line = line.strip()
            if not line: continue
            words = [w.upper() for w in line.split() if len(w) >= 2]
            for i in range(len(words)-1):
                potential_name = f"{words[i]} {words[i+1]}"
                match = match_name_to_roster(potential_name, team_roster, used_roster)
                if match and match not in matched_names:
                    matched_names.append(match)
                    used_roster.add(match)
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        return matched_names[:expected_count]
    except:
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense):
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        all_roster = roster_forwards + roster_defense
        player_names = []
        for line in lines:
            if any(x in line.upper() for x in ['IGP:', 'G:', 'A:', 'P:', 'OVERALL']): continue
            words = [w.upper() for w in line.split() if len(w) >= 3]
            if len(words) >= 2:
                for i in range(len(words)-1):
                    name = f"{words[i]} {words[i+1]}"
                    match = match_name_to_roster(name, all_roster, set(player_names))
                    if match and match not in player_names:
                        player_names.append(match)
        forwards = player_names[:12]
        defense = player_names[12:18]
        while len(forwards) < 12: forwards.append(f"PLAYER {len(forwards)+1}")
        while len(defense) < 6: defense.append(f"PLAYER {len(defense)+1}")
        return forwards, defense
    except:
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

def get_coaches_from_nhl(team_abbrev):
    coaches_list = []
    try:
        team_slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{team_slug}/team/coaches"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for img in soup.find_all('img'):
                alt = img.get('alt', '').lower()
                if any(keyword in alt for keyword in ['coach', 'assistant']):
                    name = img.get('alt', '').split('-')[0].strip()
                    role = 'HEAD COACH' if 'head' in alt else 'ASSISTANT COACH'
                    coaches_list.append({'name': name.upper(), 'role': role, 'headshot_url': img.get('src')})
                    if len(coaches_list) >= 4: break
        return coaches_list
    except: return []

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
            if len(found_teams) > 1: break
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        roster_data = {}
        r_forwards, r_defense, r_goalies = [], [], []
        for pos_key in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos_key, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                roster_data[name] = {'id': p['id'], 'num': str(p['sweaterNumber']), 'is_forward': pos_key == 'forwards'}
                if pos_key == 'forwards': r_forwards.append(name)
                elif pos_key == 'defensemen': r_defense.append(name)
                else: r_goalies.append(name)

        if combined_file:
            combined_file.seek(0)
            forwards, defensemen = extract_players_from_combined_image(combined_file, r_forwards, r_defense)
        else:
            forwards_file.seek(0); defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, r_forwards)
            defensemen = extract_players_from_image(defense_file, 6, r_defense)

        def build_list(names):
            return [{'name': n, 'number': roster_data.get(n, {'num':''})['num'], 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{roster_data[n]['id']}.png" if n in roster_data else None} for n in names]

        goalies_list = [{'name': f"#{roster_data[g]['num']} {g.split()[-1]}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{roster_data[g]['id']}.png"} for g in r_goalies[:2]]

        return jsonify({'forwards': build_list(forwards), 'defensemen': build_list(defensemen), 'goalies': goalies_list, 'coaches': get_coaches_from_nhl(team), 'team': team})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
