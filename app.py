from flask import Flask, render_template, request, jsonify
import pytesseract
from PIL import Image
import requests
import os
import time
from collections import Counter

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = '/tmp/nhl_uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_team_roster(team_abbrev):
    """Get full roster from NHL API"""
    try:
        url = f"https://api-web.nhle.com/v1/roster/{team_abbrev}/current"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            forwards = []
            defensemen = []
            
            for player in data.get('forwards', []):
                first = player.get('firstName', {}).get('default', '')
                last = player.get('lastName', {}).get('default', '')
                name = f"{first} {last}".strip().upper()
                if name:
                    forwards.append(name)
            
            for player in data.get('defensemen', []):
                first = player.get('firstName', {}).get('default', '')
                last = player.get('lastName', {}).get('default', '')
                name = f"{first} {last}".strip().upper()
                if name:
                    defensemen.append(name)
            
            return forwards, defensemen
    except Exception as e:
        print(f"Roster API Error: {e}")
    
    return [], []

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
        
        # Last name match (most important)
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
        
        # First name match
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            ocr_first = ocr_parts[0]
            roster_first = roster_parts[0]
            
            if ocr_first == roster_first:
                score += 50
            elif len(ocr_first) >= 3 and len(roster_first) >= 3:
                if ocr_first[:3] == roster_first[:3]:
                    score += 30
        
        # Full name substring match
        if ocr_name in roster_name or roster_name in ocr_name:
            score += 40
        
        if score > best_score:
            best_score = score
            best_match = roster_name
    
    # Only return match if score is decent
    if best_score >= 50:
        return best_match
    
    return None

def extract_players_from_image(image_file, expected_count, team_roster):
    """Extract player names from screenshot and match to roster"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        ocr_names = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            spaces = line.count(' ')
            
            # Good line: 20+ letters, mostly uppercase, 3+ spaces
            if alpha > 20 and upper > 15 and spaces >= 3:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha())
                    if len(clean) >= 2:
                        words.append(clean.upper())
                
                # Split into name pairs (2 words each)
                for i in range(0, len(words)-1, 2):
                    if i+1 < len(words):
                        ocr_names.append(f"{words[i]} {words[i+1]}")
        
        print(f"OCR extracted {len(ocr_names)} names: {ocr_names}")
        
        # Match OCR names to roster
        matched_names = []
        used_names = set()
        
        for ocr_name in ocr_names:
            match = match_name_to_roster(ocr_name, team_roster, used_names)
            
            if match:
                matched_names.append(match)
                used_names.add(match)
                print(f"  Matched '{ocr_name}' -> '{match}'")
            else:
                # No good match, keep OCR name
                matched_names.append(ocr_name)
                print(f"  No match for '{ocr_name}', keeping OCR")
        
        # Pad with placeholders if needed
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        
        return matched_names[:expected_count]
        
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def search_player(player_name, known_team=None):
    """Search for player using NHL API"""
    if player_name.startswith("PLAYER "):
        return None
    
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
                        return {
                            'id': player.get('playerId'),
                            'team': player.get('teamAbbrev', None),
                            'full_name': player.get('name')
                        }
                return {
                    'id': data[0].get('playerId'),
                    'team': data[0].get('teamAbbrev', None),
                    'full_name': data[0].get('name')
                }
    except:
        pass
    
    return None

def get_jersey_number(player_id, team):
    """Fetch jersey number from NHL API"""
    try:
        roster_url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(roster_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            for position_group in ['forwards', 'defensemen', 'goalies']:
                if position_group in data:
                    for player in data[position_group]:
                        if str(player.get('id')) == str(player_id):
                            return str(player.get('sweaterNumber', ''))
    except:
        pass
    
    return ''

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/lineup')
def lineup():
    return render_template('lineup.html')

@app.route('/process', methods=['POST'])
def process_lineup():
    try:
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        if not forwards_file or not defense_file:
            return jsonify({'error': 'Both screenshots required'}), 400
        
        # First, identify the team
        print("Identifying team...")
        temp_forwards = []
        temp_defensemen = []
        
        # Quick OCR to get some names for team detection
        for img in [forwards_file, defense_file]:
            img.seek(0)
            image = Image.open(img)
            text = pytesseract.image_to_string(image, config='--psm 6')
            
            for line in text.split('\n'):
                alpha = sum(1 for c in line if c.isalpha())
                if alpha > 20:
                    words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                    if len(words) >= 4:
                        name = f"{words[0]} {words[1]}"
                        temp_forwards.append(name)
                        if len(temp_forwards) >= 3:
                            break
            if len(temp_forwards) >= 3:
                break
        
        # Detect team
        found_teams = []
        for name in temp_forwards[:5]:
            result = search_player(name)
            if result and result['team']:
                found_teams.append(result['team'])
                time.sleep(0.2)
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'NHL'
        print(f"Detected team: {default_team}")
        
        # Get full roster
        roster_forwards, roster_defense = get_team_roster(default_team)
        print(f"Got roster: {len(roster_forwards)} forwards, {len(roster_defense)} defense")
        
        # Reset file pointers
        forwards_file.seek(0)
        defense_file.seek(0)
        
        # Extract with roster matching
        forwards = extract_players_from_image(forwards_file, 12, roster_forwards)
        defensemen = extract_players_from_image(defense_file, 6, roster_defense)
        
        print(f"Final forwards: {forwards}")
        print(f"Final defense: {defensemen}")
        
        # Get player data
        all_players = []
        
        for player_name in forwards + defensemen:
            result = search_player(player_name, default_team)
            
            if result and result['id'] and result['team']:
                jersey_number = get_jersey_number(result['id'], result['team'])
                
                all_players.append({
                    'name': player_name,
                    'id': result['id'],
                    'team': result['team'],
                    'number': jersey_number,
                    'is_forward': player_name in forwards,
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{result['team']}/{result['id']}.png"
                })
            else:
                all_players.append({
                    'name': player_name,
                    'id': None,
                    'team': default_team,
                    'number': '',
                    'is_forward': player_name in forwards,
                    'headshot_url': None
                })
            
            time.sleep(0.2)
        
        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'team': default_team
        })
        
    except Exception as e:
        print(f"Process error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
