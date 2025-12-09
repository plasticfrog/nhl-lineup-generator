from flask import Flask, render_template, request, jsonify, send_file
import pytesseract
from PIL import Image
import requests
import os
import io
import base64
import time
from collections import Counter

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = '/tmp/nhl_uploads'

# Create upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def extract_players_from_image(image_file):
    """Extract player names from screenshot using OCR"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        all_names = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip lines with these patterns
            skip_patterns = ['defensive', 'pairing', 'forward', '===', '---']
            if any(skip in line.lower() for skip in skip_patterns):
                continue
            
            # Must have significant alphabetic content
            alpha_count = sum(1 for c in line if c.isalpha())
            if alpha_count < 10:  # Need at least 10 letters for a name line
                continue
            
            # Split into potential name chunks (2-3 word patterns)
            words = line.split()
            
            # Try to extract names (pattern: FIRSTNAME LASTNAME or FIRSTNAME MIDDLE LASTNAME)
            i = 0
            while i < len(words):
                word = words[i]
                
                # Skip if not mostly letters
                if not word or sum(c.isalpha() for c in word) < 2:
                    i += 1
                    continue
                
                # Try to form a 2 or 3 word name
                name_words = []
                for j in range(i, min(i+3, len(words))):
                    w = words[j]
                    alpha_chars = sum(1 for c in w if c.isalpha())
                    if alpha_chars >= 2 and alpha_chars / len(w) >= 0.5:
                        clean = ''.join(c for c in w if c.isalpha() or c in ['-', "'"])
                        if clean:
                            name_words.append(clean.upper())
                
                # If we have 2-3 words, it's likely a name
                if len(name_words) >= 2:
                    full_name = ' '.join(name_words[:3])  # Max 3 words
                    if len(full_name.replace(' ', '')) >= 8:  # At least 8 letters total
                        all_names.append(full_name)
                        i += len(name_words)
                    else:
                        i += 1
                else:
                    i += 1
        
        return all_names
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return []

def search_player(player_name, known_team=None):
    """Search for player using NHL API"""
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
        # Get uploaded files
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        if not forwards_file or not defense_file:
            return jsonify({'error': 'Both screenshots required'}), 400
        
        # Extract players
        forwards = extract_players_from_image(forwards_file)
        defensemen = extract_players_from_image(defense_file)
        
        # Get player data
        all_players = []
        found_teams = []
        
        # First pass: identify team
        for player_name in forwards + defensemen:
            result = search_player(player_name)
            if result and result['team']:
                found_teams.append(result['team'])
                time.sleep(0.3)
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'UNK'
        
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
                all_players.append({
                    'name': player_name,
                    'id': None,
                    'team': default_team,
                    'number': '',
                    'is_forward': player_name in forwards,
                    'headshot_url': None
                })
            
            time.sleep(0.3)
        
        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'team': default_team
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Get port from environment variable (Railway uses PORT)
    port = int(os.environ.get('PORT', 5000))
    # Run on all interfaces so it's accessible on Railway
    app.run(host='0.0.0.0', port=port, debug=False)
