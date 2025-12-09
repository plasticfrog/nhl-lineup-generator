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

def extract_players_from_image(image_file, expected_count):
    """Extract player names - ALWAYS returns exactly expected_count names"""
    try:
        image = Image.open(image_file)
        
        # Try multiple OCR modes for reliability
        texts = []
        for psm in [6, 11, 3]:
            try:
                text = pytesseract.image_to_string(image, config=f'--psm {psm}')
                texts.append(text)
            except:
                pass
        
        # Combine all OCR attempts
        all_text = '\n'.join(texts)
        lines = [l.strip() for l in all_text.split('\n') if l.strip()]
        
        # Extract all potential names
        potential_names = []
        seen = set()
        
        for line in lines:
            # Skip obvious non-names
            if any(skip in line.lower() for skip in ['defensive', 'pairing', 'forward', 'line', 'http', 'www']):
                continue
            
            # Count letters
            alpha_count = sum(1 for c in line if c.isalpha())
            if alpha_count < 6:
                continue
            
            # Extract word sequences
            words = []
            for word in line.split():
                # Keep only alphabetic characters
                clean = ''.join(c for c in word if c.isalpha() or c == '-')
                if len(clean) >= 2:
                    words.append(clean.upper())
            
            # Try to form 2-word or 3-word names
            if len(words) >= 2:
                # Try all combinations of 2-3 consecutive words
                for i in range(len(words)):
                    # 2-word name
                    if i + 1 < len(words):
                        name = f"{words[i]} {words[i+1]}"
                        if len(name.replace(' ', '')) >= 6 and name not in seen:
                            potential_names.append(name)
                            seen.add(name)
                    
                    # 3-word name
                    if i + 2 < len(words):
                        name = f"{words[i]} {words[i+1]} {words[i+2]}"
                        if len(name.replace(' ', '')) >= 8 and name not in seen:
                            potential_names.append(name)
                            seen.add(name)
        
        # Remove obvious duplicates and substrings
        unique_names = []
        for name in potential_names:
            # Check if it's a substring of an existing name
            is_substring = False
            for existing in unique_names:
                if name in existing or existing in name:
                    if len(name) > len(existing):
                        # Replace with longer version
                        unique_names.remove(existing)
                        unique_names.append(name)
                    is_substring = True
                    break
            
            if not is_substring:
                unique_names.append(name)
        
        # Take first expected_count names
        result = unique_names[:expected_count]
        
        # Fill with placeholders if we don't have enough
        while len(result) < expected_count:
            result.append(f"PLAYER {len(result) + 1}")
        
        return result[:expected_count]
        
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        # Return placeholders on error
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def search_player(player_name, known_team=None):
    """Search for player using NHL API"""
    # Skip placeholders
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
                # Return first result as fallback
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
        
        # Extract EXACTLY 12 forwards and 6 defense
        forwards = extract_players_from_image(forwards_file, 12)
        defensemen = extract_players_from_image(defense_file, 6)
        
        print(f"Extracted forwards: {forwards}")
        print(f"Extracted defensemen: {defensemen}")
        
        # Get player data
        all_players = []
        found_teams = []
        
        # First pass: identify team
        for player_name in forwards + defensemen:
            result = search_player(player_name)
            if result and result['team']:
                found_teams.append(result['team'])
                time.sleep(0.2)
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'NHL'
        
        # Second pass: get all player data
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
                # Placeholder - no API match
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
