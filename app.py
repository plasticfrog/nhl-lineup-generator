--- START OF FILE app.py ---

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

def get_ordered_lines_from_image(image):
    """
    Core OCR logic: Extracts text while strictly maintaining Left-to-Right and Top-to-Bottom order
    using spatial coordinates. This prevents column-skipping or swapped horizontal names.
    """
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    n_boxes = len(data['text'])
    
    words = []
    for i in range(n_boxes):
        text = data['text'][i].strip()
        if text:
            words.append({
                'text': text,
                'left': data['left'][i],
                'top': data['top'][i],
                'height': data['height'][i]
            })
    
    if not words:
        return []

    words.sort(key=lambda x: x['top'])
    
    lines_list = []
    if words:
        current_line = [words[0]]
        for i in range(1, len(words)):
            prev_word = current_line[-1]
            if abs(words[i]['top'] - prev_word['top']) < (prev_word['height'] / 1.5):
                current_line.append(words[i])
            else:
                current_line.sort(key=lambda x: x['left'])
                lines_list.append(" ".join([w['text'] for w in current_line]))
                current_line = [words[i]]
        
        current_line.sort(key=lambda x: x['left'])
        lines_list.append(" ".join([w['text'] for w in current_line]))
    
    return lines_list

def match_name_to_roster(ocr_name, roster_list, used_names):
    """Find best matching name from roster using fuzzy matching with OCR character fixes"""
    ocr_name = ocr_name.replace('3', 'S').replace('0', 'O').replace('1', 'I').replace('4', 'A').replace('5', 'S')
    
    best_match = None
    best_score = 0
    
    ocr_parts = ocr_name.split()
    
    for roster_name in roster_list:
        if roster_name in used_names:
            continue
        
        roster_parts = roster_name.split()
        score = 0
        
        if len(ocr_parts) > 0 and len(roster_parts) > 0:
            ocr_last = ocr_parts[-1]
            roster_last = roster_parts[-1]
            
            if ocr_last == roster_last:
                score += 100
            elif ocr_last in roster_last or roster_last in ocr_last:
                score += 80
            elif len(ocr_last) >= 4 and len(roster_last) >= 4:
                if ocr_last[:4] == roster_last[:4]:
                    score += 60
        
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            ocr_first = ocr_parts[0]
            roster_first = roster_parts[0]
            
            if ocr_first == roster_first:
                score += 50
            elif len(ocr_first) >= 3 and len(roster_first) >= 3:
                if ocr_first[:3] == roster_first[:3]:
                    score += 30
        
        if ocr_name in roster_name or roster_name in ocr_name:
            score += 40
        
        if score > best_score:
            best_score = score
            best_match = roster_name
    
    if best_score >= 50:
        return best_match
    
    return None

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense, roster_goalies):
    """Extract players from single image with coordinate-based sorting"""
    try:
        print("\n--- PROCESSING COMBINED IMAGE ---")
        image = Image.open(image_file)
        lines = get_ordered_lines_from_image(image)
        
        extracted_skaters = []
        all_skater_roster = roster_forwards + roster_defense
        used_skaters = set()
        used_goalies = set()
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line or any(x in line.upper() for x in ['FORWARD', 'DEFENSE', 'GOALTENDER', 'OVERALL', 'HOME', 'ROAD', 'COACH', 'SCRATCH', 'LINES ARE']):
                i += 1
                continue
            
            if any(x in line for x in ['iP:', 'IGP:', 'G:', 'A:', 'P:', 'GAA:', 'SVP:', 'H:', 'W:', 'Ace:']):
                i += 1
                continue
            
            words = []
            for word in line.split():
                clean = ''.join(c for c in word if c.isalpha() or c.isdigit() or c in ["-", "'"])
                if len(clean) >= 2:
                    words.append(clean.upper())
            
            if len(words) >= 2 and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                next_words = []
                for word in next_line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c.isdigit() or c in ["-", "'"])
                    if len(clean) >= 2:
                        next_words.append(clean.upper())
                
                if len(next_words) >= 2:
                    for j in range(min(len(words), len(next_words))):
                        f_name = words[j].replace('3', 'S').replace('5', 'S')
                        l_name = next_words[j]
                        full_name = f"{f_name} {l_name}"
                        
                        goalie_match = match_name_to_roster(full_name, roster_goalies, used_goalies)
                        if goalie_match:
                            print(f"[GOALIE FILTER] {goalie_match}")
                            used_goalies.add(goalie_match)
                            continue 
                        
                        skater_match = match_name_to_roster(full_name, all_skater_roster, used_skaters)
                        if skater_match:
                            print(f"[MATCH FOUND] {skater_match}")
                            extracted_skaters.append(skater_match)
                            used_skaters.add(skater_match)
                        else:
                            extracted_skaters.append(full_name)
                    
                    i += 2
                    continue
            i += 1
        
        forwards = extracted_skaters[:12]
        defense = extracted_skaters[12:18]
        while len(forwards) < 12: forwards.append(f"PLAYER {len(forwards)+1}")
        while len(defense) < 6: defense.append(f"PLAYER {len(defense)+1}")
        return forwards, defense
        
    except Exception as e:
        print(f"[ERROR] Combined extraction failed: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

def extract_players_from_image(image_file, expected_count, team_roster, type_label="SKATER"):
    """Extract player names from jerseys with strict Left-to-Right sorting"""
    try:
        print(f"\n--- PROCESSING {type_label} IMAGE ---")
        image = Image.open(image_file)
        lines = get_ordered_lines_from_image(image)
        matched_names = []
        used_roster = set()
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            alpha = sum(1 for c in line if c.isalpha())
            if alpha > 5:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c == '-')
                    if clean: words.append(clean.upper())
                
                for i in range(len(words)-1):
                    potential_name = f"{words[i]} {words[i+1]}"
                    match = match_name_to_roster(potential_name, team_roster, used_roster)
                    if match and match not in matched_names:
                        print(f"[MATCH FOUND] {match}")
                        matched_names.append(match)
                        used_roster.add(match)
        
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        return matched_names[:expected_count]
    except Exception as e:
        print(f"[ERROR] {type_label} extraction failed: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def search_player(player_name):
    """Search for player using NHL API"""
    if not player_name or player_name.startswith("PLAYER "): return None
    try:
        search_name = player_name.lower().replace(' ', '%20')
        search_url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={search_name}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(search_url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 0:
                return {'id': data[0].get('playerId'), 'team': data[0].get('teamAbbrev', None), 'full_name': data[0].get('name')}
    except: pass
    return None

def get_coaches_from_nhl(team_abbrev):
    """Scrape coaches from NHL.com team coaches page"""
    coaches_list = []
    try:
        team_slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{team_slug}/team/coaches"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            imgs = soup.find_all('img')
            for img in imgs:
                alt = img.get('alt', '').lower()
                src = img.get('src', '')
                if any(keyword in alt for keyword in ['coach', 'assistant', 'goaltending']):
                    name = img.get('alt', '').split('-')[0].strip()
                    role = 'HEAD COACH' if 'head' in alt else 'ASSISTANT COACH'
                    coaches_list.append({'name': name.upper(), 'role': role, 'headshot_url': src})
                    if len(coaches_list) >= 4: break
        return coaches_list
    except: return []

def extract_roster_from_screenshot(image_file):
    """Used for Number Entry method"""
    try:
        print("\n--- EXTRACTING ROSTER FROM SCREENSHOT ---")
        image = Image.open(image_file)
        lines = get_ordered_lines_from_image(image)
        roster = {}
        coaches = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit():
                number = parts[0]
                name = ' '.join(parts[2:]).upper() if len(parts[1])==1 else ' '.join(parts[1:]).upper()
                roster[number] = {'name': name}
                print(f"[ROSTER] #{number} {name}")
        return roster, coaches
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    """Used for Number Entry method"""
    if text: return re.findall(r'\d+', text)
    if image_file:
        try:
            image = Image.open(image_file)
            return re.findall(r'\d+', pytesseract.image_to_string(image))
        except: return []
    return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/numbers')
def numbers_page():
    return render_template('index_numbers.html')

@app.route('/lineup')
def lineup():
    return render_template('lineup.html')

@app.route('/process', methods=['POST'])
def process_lineup():
    try:
        combined_file = request.files.get('combined')
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        sample_file = combined_file if combined_file else forwards_file
        sample_file.seek(0)
        image = Image.open(sample_file)
        lines = get_ordered_lines_from_image(image)
        
        temp_names = []
        for line in lines:
            words = [w for w in line.split() if len(w) > 3]
            for i in range(len(words)-1):
                temp_names.append(f"{words[i]} {words[i+1]}")
            if len(temp_names) > 15: break
        
        found_teams = []
        for name in temp_names:
            res = search_player(name)
            if res and res['team']: found_teams.append(res['team'])
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        print(f"\n--- DETECTED TEAM: {default_team} ---")
        
        roster_url = f"https://api-web.nhle.com/v1/roster/{default_team}/current"
        r_json = requests.get(roster_url, timeout=10).json()
        roster_data = {}
        goalies_list = []
        
        for pos in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos, []):
                full_name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos == 'goalies':
                    goalies_list.append({
                        'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}",
                        'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{default_team}/{p['id']}.png"
                    })
                else:
                    roster_data[full_name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos=='forwards'}

        roster_forwards = [n for n, d in roster_data.items() if d['is_forward']]
        roster_defense = [n for n, d in roster_data.items() if not d['is_forward']]
        roster_goalie_names = [g['name'].split()[-1] for g in goalies_list]

        if combined_file:
            combined_file.seek(0)
            forwards, defensemen = extract_players_from_combined_image(combined_file, roster_forwards, roster_defense, roster_goalie_names)
        else:
            forwards_file.seek(0); defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, roster_forwards, "FORWARDS")
            defensemen = extract_players_from_image(defense_file, 6, roster_defense, "DEFENSE")

        all_players = []
        for name in forwards + defensemen:
            info = roster_data.get(name, {'id': None, 'number': ''})
            all_players.append({
                'name': name, 'number': info['number'], 'is_forward': name in forwards,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{default_team}/{info['id']}.png" if info['id'] else None
            })

        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'goalies': goalies_list[:2],
            'coaches': get_coaches_from_nhl(default_team),
            'team': default_team
        })
    except Exception as e:
        print(f"[CRITICAL ERROR] {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    try:
        team = request.form.get('team')
        roster_screenshot = request.files.get('roster_screenshot')
        lines_text = request.form.get('lines_text')
        lines_screenshot = request.files.get('lines_screenshot')
        
        roster, _ = extract_roster_from_screenshot(roster_screenshot)
        nums = extract_line_numbers(text=lines_text, image_file=lines_screenshot)
        
        api_roster = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current").json()
        goalies = [{'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{p['id']}.png"} for p in api_roster.get('goalies', [])]
        
        final_players = []
        for i, n in enumerate(nums[:18]):
            name = roster.get(n, {}).get('name', f"PLAYER #{n}")
            pid = next((p['id'] for group in ['forwards', 'defensemen'] for p in api_roster.get(group, []) if str(p['sweaterNumber']) == n), None)
            final_players.append({
                'name': name, 'number': n, 'is_forward': i < 12,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{team}/{pid}.png" if pid else None
            })

        return jsonify({
            'forwards': [p for p in final_players if p['is_forward']],
            'defensemen': [p for p in final_players if not p['is_forward']],
            'goalies': goalies[:2],
            'coaches': get_coaches_from_nhl(team),
            'team': team
        })
    except Exception as e:
        print(f"[CRITICAL ERROR] {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
