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

def match_name_to_roster(ocr_name, roster_list, used_names):
    """Find best matching name from roster using fuzzy matching"""
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

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense):
    """Extract players from single image and maintain exact visual order"""
    try:
        image = Image.open(image_file)
        # Using PSM 6 to assume a single uniform block of text for better sequence detection
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        all_detected_names = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Skip noise lines
            if not line or any(x in line.upper() for x in ['FORWARD', 'DEFENSE', 'GOALTENDER', 'OVERALL', 'HOME', 'ROAD', 'COACH', 'SCRATCH']):
                i += 1
                continue
                
            # Clean common stats noise
            if any(x in line for x in ['iP:', 'IGP:', 'G:', 'A:', 'P:', 'GAA:', 'SVP:', 'H:', 'W:', 'Ace:']):
                i += 1
                continue

            # Look for words that are likely names (Capitalized, min length)
            words = []
            for word in line.split():
                clean = ''.join(c for c in word if c.isalpha() or c == '-' or c == "'")
                if len(clean) >= 3:
                    words.append(clean.upper())

            # Detect same-line names (First Last First Last) or split-line names
            if len(words) >= 2:
                # Check next line to see if it's a "Last Name" line
                next_line_words = []
                if i + 1 < len(lines):
                    for word in lines[i+1].split():
                        clean = ''.join(c for c in word if c.isalpha() or c == '-' or c == "'")
                        if len(clean) >= 3: next_line_words.append(clean.upper())
                
                # Logic A: First names on this line, Last names on the next line
                if len(words) == len(next_line_words) and len(words) >= 2:
                    for j in range(len(words)):
                        all_detected_names.append(f"{words[j]} {next_line_words[j]}")
                    i += 2
                    continue
                # Logic B: Full names on this line (First Last First Last)
                else:
                    for j in range(0, len(words) - 1, 2):
                        all_detected_names.append(f"{words[j]} {words[j+1]}")
                    i += 1
                    continue
            i += 1

        # Match detected names to the specific roster groups to keep order
        final_forwards = []
        final_defense = []
        used_names = set()
        
        # We process the names in the order found
        for detected in all_detected_names:
            # Check if it's a forward
            f_match = match_name_to_roster(detected, roster_forwards, used_names)
            if f_match and len(final_forwards) < 12:
                final_forwards.append(f_match)
                used_names.add(f_match)
                continue
            
            # Check if it's defense
            d_match = match_name_to_roster(detected, roster_defense, used_names)
            if d_match and len(final_defense) < 6:
                final_defense.append(d_match)
                used_names.add(d_match)

        # Pad with placeholders if OCR missed anyone
        while len(final_forwards) < 12: final_forwards.append(f"PLAYER {len(final_forwards)+1}")
        while len(final_defense) < 6: final_defense.append(f"PLAYER {len(final_defense)+1}")
        
        return final_forwards, final_defense
        
    except Exception as e:
        print(f"Combined OCR Error: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

def extract_players_from_image(image_file, expected_count, team_roster):
    """Extract player names from separate image in exact sequential order"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        matched_names = []
        used_roster = set()
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Extract name candidates
            words = []
            for word in line.split():
                clean = ''.join(c for c in word if c.isalpha() or c == '-')
                if len(clean) >= 3: words.append(clean.upper())
            
            # Match sequentially
            if len(words) >= 2:
                # Try pairing words as names
                for i in range(len(words)-1):
                    potential = f"{words[i]} {words[i+1]}"
                    match = match_name_to_roster(potential, team_roster, used_roster)
                    if match and match not in matched_names:
                        matched_names.append(match)
                        used_roster.add(match)
        
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        
        return matched_names[:expected_count]
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def search_player(player_name, known_team=None):
    if player_name.startswith("PLAYER "): return None
    try:
        search_name = player_name.lower().replace(' ', '%20')
        search_url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={search_name}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(search_url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 0:
                for player in data:
                    player_full_name = player.get('name', '').upper()
                    if player_name.upper() in player_full_name or player_full_name in player_name.upper():
                        return {'id': player.get('playerId'), 'team': player.get('teamAbbrev', None), 'full_name': player.get('name')}
                return {'id': data[0].get('playerId'), 'team': data[0].get('teamAbbrev', None), 'full_name': data[0].get('name')}
    except: pass
    return None

def get_coaches_from_nhl(team_abbrev):
    coaches_list = []
    try:
        team_slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{team_slug}/team/coaches"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            imgs = soup.find_all('img')
            for img in imgs:
                alt = img.get('alt', '').lower()
                src = img.get('src', '')
                if any(keyword in alt for keyword in ['coach', 'assistant', 'goaltending']):
                    name_match = img.get('alt', '').split('-')[0].strip() if '-' in img.get('alt', '') else img.get('alt', '').strip()
                    role = 'COACH'
                    if 'head coach' in alt: role = 'HEAD COACH'
                    elif 'assistant' in alt: role = 'ASSISTANT COACH'
                    elif 'goaltending' in alt: role = 'GOALTENDING COACH'
                    coaches_list.append({'name': name_match.upper(), 'role': role, 'headshot_url': src})
                    if len(coaches_list) >= 3: break
        return coaches_list
    except: return []

def extract_roster_from_screenshot(image_file):
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        roster = {}
        coaches = []
        in_coaching_section = False
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5: continue
            if any(x in line.lower() for x in ['head coach', 'assistant coach', 'coach']):
                in_coaching_section = True
                parts = line.split()
                if len(parts) >= 3:
                    coach_name = ' '.join(parts[-2:])
                    coaches.append({'role': 'HEAD COACH' if 'head' in line.lower() else 'ASSISTANT COACH', 'name': coach_name.upper()})
                continue
            if in_coaching_section:
                parts = [p for p in line.split() if sum(c.isalpha() for c in p) > 2]
                if len(parts) >= 2:
                    coach_name = ' '.join(parts[-2:])
                    coaches.append({'role': 'COACH', 'name': coach_name.upper()})
                continue
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit(): continue
            number = parts[0]
            position = 'F'
            name_start_idx = 1
            if len(parts[1]) == 1 and parts[1] in ['C', 'L', 'R', 'D', 'W']:
                position = 'F' if parts[1] in ['C', 'L', 'R', 'W'] else 'D'
                name_start_idx = 2
            name_parts = []
            for i in range(name_start_idx, len(parts)):
                if parts[i].isdigit(): break
                name_parts.append(parts[i])
            if len(name_parts) >= 2:
                roster[number] = {'name': ' '.join(name_parts).upper(), 'position': position}
        return roster, coaches
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    numbers = []
    if text:
        text = re.sub(r'^\w+\s*\n', '', text)
        for line in text.split('\n'):
            line = line.replace('/', '-')
            numbers.extend(re.findall(r'\d+', line))
    elif image_file:
        try:
            image = Image.open(image_file)
            text = pytesseract.image_to_string(image, config='--psm 6')
            numbers.extend(re.findall(r'\d+', text))
        except: pass
    return numbers

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
        
        if not combined_file and not (forwards_file and defense_file):
            return jsonify({'error': 'Screenshots required'}), 400
        
        # Detect team logic
        temp_names = []
        if combined_file:
            combined_file.seek(0)
            text = pytesseract.image_to_string(Image.open(combined_file), config='--psm 6')
            lines = text.split('\n')
            for line in lines[5:]:
                alpha = sum(1 for c in line if c.isalpha())
                if alpha > 20:
                    words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                    if len(words) >= 4:
                        temp_names.append(f"{words[0]} {words[1]}")
                        if len(temp_names) >= 5: break
        else:
            for img in [forwards_file, defense_file]:
                img.seek(0)
                text = pytesseract.image_to_string(Image.open(img), config='--psm 6')
                lines = text.split('\n')
                for line in lines[3:]:
                    alpha = sum(1 for c in line if c.isalpha())
                    if alpha > 20:
                        words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                        if len(words) >= 4:
                            temp_names.append(f"{words[0]} {words[1]}")
                            if len(temp_names) >= 5: break
                if len(temp_names) >= 5: break
        
        found_teams = []
        for name in temp_names[:8]:
            result = search_player(name)
            if result and result['team']: found_teams.append(result['team'])
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        
        # Get roster
        roster_url = f"https://api-web.nhle.com/v1/roster/{default_team}/current"
        roster_json = requests.get(roster_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()
        
        roster_data = {}
        goalies_list = []
        for position_group in ['forwards', 'defensemen']:
            for player in roster_json.get(position_group, []):
                full_name = f"{player['firstName']['default']} {player['lastName']['default']}".strip().upper()
                roster_data[full_name] = {'id': player.get('id'), 'number': str(player.get('sweaterNumber', '')), 'is_forward': position_group == 'forwards'}
        
        for player in roster_json.get('goalies', [])[:2]:
            last = player.get('lastName', {}).get('default', '').upper()
            num = str(player.get('sweaterNumber', ''))
            goalies_list.append({
                'name': f"#{num} {last}" if num else last,
                'id': player.get('id'),
                'number': num,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{default_team}/{player.get('id')}.png"
            })
        
        coaches_list = get_coaches_from_nhl(default_team)
        roster_forwards = [name for name, data in roster_data.items() if data['is_forward']]
        roster_defense = [name for name, data in roster_data.items() if not data['is_forward']]
        
        if combined_file:
            combined_file.seek(0)
            forwards, defensemen = extract_players_from_combined_image(combined_file, roster_forwards, roster_defense)
        else:
            forwards_file.seek(0); defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, roster_forwards)
            defensemen = extract_players_from_image(defense_file, 6, roster_defense)
        
        all_players = []
        for player_name in forwards + defensemen:
            info = roster_data.get(player_name, {'id': None, 'number': '', 'is_forward': player_name in forwards})
            all_players.append({
                'name': player_name,
                'id': info['id'],
                'team': default_team,
                'number': info['number'],
                'is_forward': info['is_forward'],
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{default_team}/{info['id']}.png" if info['id'] else None
            })
        
        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'goalies': goalies_list,
            'coaches': coaches_list,
            'team': default_team
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/process_numbers', methods=['POST'])
def process_numbers():
    try:
        team = request.form.get('team')
        lines_text = request.form.get('lines_text')
        lines_screenshot = request.files.get('lines_screenshot')
        roster_screenshot = request.files.get('roster_screenshot')
        
        if not team or not roster_screenshot: return jsonify({'error': 'Missing data'}), 400
        
        roster_screenshot.seek(0)
        roster, coaches = extract_roster_from_screenshot(roster_screenshot)
        
        if lines_screenshot:
            lines_screenshot.seek(0)
            jersey_numbers = extract_line_numbers(image_file=lines_screenshot)
        else:
            jersey_numbers = extract_line_numbers(text=lines_text)
            
        api_roster_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current", timeout=10).json()
        api_roster = {}
        goalies_list = []
        
        for pg in ['forwards', 'defensemen']:
            for p in api_roster_json.get(pg, []):
                api_roster[str(p.get('sweaterNumber', ''))] = {'name': f"{p['firstName']['default']} {p['lastName']['default']}".upper(), 'id': p.get('id'), 'is_forward': pg == 'forwards'}
        
        for p in api_roster_json.get('goalies', [])[:2]:
            last = p.get('lastName', {}).get('default', '').upper()
            num = str(p.get('sweaterNumber', ''))
            goalies_list.append({'name': f"#{num} {last}" if num else last, 'id': p.get('id'), 'number': num, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{p.get('id')}.png"})
        
        final_coaches = get_coaches_from_nhl(team) or coaches
        all_players = []
        for i, num in enumerate(jersey_numbers[:18]):
            p_api = api_roster.get(num)
            name = p_api['name'] if p_api else (roster.get(num, {}).get('name') or f"PLAYER #{num}")
            all_players.append({
                'name': name, 'id': p_api['id'] if p_api else None, 'team': team, 'number': num,
                'is_forward': i < 12, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{p_api['id']}.png" if p_api else None
            })
            
        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'goalies': goalies_list,
            'coaches': final_coaches[:3],
            'team': team
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
