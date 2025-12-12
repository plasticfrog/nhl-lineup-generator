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
    """Extract players from single image with forwards and defense in multi-column layout"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        print("Combined image OCR:")
        print(text)
        print("\n" + "="*70)
        
        lines = text.split('\n')
        
        # Extract player names in order (OCR reads left-to-right)
        all_names = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip headers and stats lines
            if any(keyword in line.upper() for keyword in ['FORWARD', 'DEFENSE', 'GOALTENDER', 'OVERALL', 'HOME', 'ROAD', 'COACH', 'SCRATCH', 'AT ']):
                continue
            
            # Skip stat lines (contain lots of numbers like "iP: 31 G7 A:1 P:18")
            if 'iP:' in line or 'G:' in line or 'A:' in line or 'GAA:' in line or 'SVP:' in line:
                continue
            
            # Skip lines with physical stats (height/weight)
            if 'H:' in line or 'W:' in line or 'Ace:' in line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            
            # Good name line: lots of uppercase letters, few numbers
            if alpha > 10 and upper > 8:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c == '-')
                    if len(clean) >= 3:  # Min 3 letters
                        words.append(clean.upper())
                
                # Process words - each pair is likely a full name
                for i in range(0, len(words)-1, 2):
                    if i+1 < len(words):
                        name = f"{words[i]} {words[i+1]}"
                        all_names.append(name)
                        print(f"  Extracted: {name}")
        
        print(f"\nTotal names extracted: {len(all_names)}")
        print(f"Names: {all_names}")
        
        # Match to roster
        matched_forwards = []
        matched_defense = []
        used_roster = set()
        
        # First 12 names should be forwards
        for name in all_names[:12]:
            match = match_name_to_roster(name, roster_forwards, used_roster)
            if match:
                matched_forwards.append(match)
                used_roster.add(match)
                print(f"  Forward: '{name}' -> '{match}'")
            else:
                matched_forwards.append(name)
                print(f"  Forward: '{name}' (no match)")
        
        # Next 6 names should be defense
        for name in all_names[12:18]:
            match = match_name_to_roster(name, roster_defense, used_roster)
            if match:
                matched_defense.append(match)
                used_roster.add(match)
                print(f"  Defense: '{name}' -> '{match}'")
            else:
                matched_defense.append(name)
                print(f"  Defense: '{name}' (no match)")
        
        # Pad if needed
        while len(matched_forwards) < 12:
            matched_forwards.append(f"PLAYER {len(matched_forwards)+1}")
        
        while len(matched_defense) < 6:
            matched_defense.append(f"PLAYER {len(matched_defense)+1}")
        
        return matched_forwards[:12], matched_defense[:6]
        
    except Exception as e:
        print(f"Combined OCR Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

def extract_players_from_image(image_file, expected_count, team_roster):
    """Extract player names from separate forwards or defense image"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        matched_names = []
        used_roster = set()
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            spaces = line.count(' ')
            
            if alpha > 15 and upper > 10 and spaces >= 2:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c == '-')
                    if clean:
                        words.append(clean.upper())
                
                for i in range(len(words)-1):
                    potential_name = f"{words[i]} {words[i+1]}"
                    
                    match = match_name_to_roster(potential_name, team_roster, used_roster)
                    
                    if match and match not in matched_names:
                        matched_names.append(match)
                        used_roster.add(match)
                        print(f"  Matched '{potential_name}' -> '{match}'")
        
        print(f"Matched {len(matched_names)} players from OCR")
        
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        
        return matched_names[:expected_count]
        
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(expected_count)]
    """Extract players from single image with forwards and defense in multi-column layout"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        print("Combined image OCR:")
        print(text)
        print("\n" + "="*70)
        
        lines = text.split('\n')
        
        # Extract player names in order (OCR reads left-to-right)
        all_names = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip headers and stats lines
            if any(keyword in line.upper() for keyword in ['FORWARD', 'DEFENSE', 'GOALTENDER', 'OVERALL', 'HOME', 'ROAD', 'COACH', 'SCRATCH', 'AT ']):
                continue
            
            # Skip stat lines (contain lots of numbers like "iP: 31 G7 A:1 P:18")
            if 'iP:' in line or 'G:' in line or 'A:' in line or 'GAA:' in line or 'SVP:' in line:
                continue
            
            # Skip lines with physical stats (height/weight)
            if 'H:' in line or 'W:' in line or 'Ace:' in line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            
            # Good name line: lots of uppercase letters, few numbers
            if alpha > 10 and upper > 8:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c == '-')
                    if len(clean) >= 3:  # Min 3 letters
                        words.append(clean.upper())
                
                # Process words - each pair is likely a full name
                for i in range(0, len(words)-1, 2):
                    if i+1 < len(words):
                        name = f"{words[i]} {words[i+1]}"
                        all_names.append(name)
                        print(f"  Extracted: {name}")
        
        print(f"\nTotal names extracted: {len(all_names)}")
        print(f"Names: {all_names}")
        
        # Match to roster
        matched_forwards = []
        matched_defense = []
        used_roster = set()
        
        # First 12 names should be forwards
        for name in all_names[:12]:
            match = match_name_to_roster(name, roster_forwards, used_roster)
            if match:
                matched_forwards.append(match)
                used_roster.add(match)
                print(f"  Forward: '{name}' -> '{match}'")
            else:
                matched_forwards.append(name)
                print(f"  Forward: '{name}' (no match)")
        
        # Next 6 names should be defense
        for name in all_names[12:18]:
            match = match_name_to_roster(name, roster_defense, used_roster)
            if match:
                matched_defense.append(match)
                used_roster.add(match)
                print(f"  Defense: '{name}' -> '{match}'")
            else:
                matched_defense.append(name)
                print(f"  Defense: '{name}' (no match)")
        
        # Pad if needed
        while len(matched_forwards) < 12:
            matched_forwards.append(f"PLAYER {len(matched_forwards)+1}")
        
        while len(matched_defense) < 6:
            matched_defense.append(f"PLAYER {len(matched_defense)+1}")
        
        return matched_forwards[:12], matched_defense[:6]
        
    except Exception as e:
        print(f"Combined OCR Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]
    """Extract player names and validate against roster"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        matched_names = []
        used_roster = set()
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            spaces = line.count(' ')
            
            if alpha > 15 and upper > 10 and spaces >= 2:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha() or c == '-')
                    if clean:
                        words.append(clean.upper())
                
                for i in range(len(words)-1):
                    potential_name = f"{words[i]} {words[i+1]}"
                    
                    match = match_name_to_roster(potential_name, team_roster, used_roster)
                    
                    if match and match not in matched_names:
                        matched_names.append(match)
                        used_roster.add(match)
                        print(f"  Matched '{potential_name}' -> '{match}'")
        
        print(f"Matched {len(matched_names)} players from OCR")
        
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

def get_coaches_from_nhl(team_abbrev):
    """Scrape coaches from NHL.com team coaches page"""
    coaches_list = []
    
    try:
        team_slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        url = f"https://www.nhl.com/{team_slug}/team/coaches"
        
        print(f"Scraping coaches from: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for coach images with alt text
            imgs = soup.find_all('img')
            for img in imgs:
                alt = img.get('alt', '').lower()
                src = img.get('src', '')
                
                # Check if this is a coach image
                if any(keyword in alt for keyword in ['coach', 'assistant', 'goaltending']):
                    # Extract coach name from alt text
                    name_match = img.get('alt', '').split('-')[0].strip() if '-' in img.get('alt', '') else img.get('alt', '').strip()
                    
                    # Determine role
                    role = 'COACH'
                    alt_lower = alt.lower()
                    if 'head coach' in alt_lower:
                        role = 'HEAD COACH'
                    elif 'assistant' in alt_lower:
                        role = 'ASSISTANT COACH'
                    elif 'goaltending' in alt_lower:
                        role = 'GOALTENDING COACH'
                    
                    headshot_url = src
                    
                    coaches_list.append({
                        'name': name_match.upper(),
                        'role': role,
                        'headshot_url': headshot_url
                    })
                    
                    print(f"Found coach: {name_match} - {role} - {headshot_url}")
                    
                    if len(coaches_list) >= 3:
                        break
        
        return coaches_list[:3]
        
    except Exception as e:
        print(f"NHL.com scraping error: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def extract_roster_from_screenshot(image_file):
    """Extract player roster from stats table screenshot"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        roster = {}
        coaches = []
        
        print("Roster OCR output:")
        print(text)
        print("\n" + "="*70)
        
        in_coaching_section = False
        
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            
            # Check if we hit coaching section
            if any(x in line.lower() for x in ['head coach', 'assistant coach', 'coach']):
                in_coaching_section = True
                # Try to extract coach name from same line
                parts = line.split()
                if len(parts) >= 3:
                    coach_name = ' '.join(parts[-2:])
                    if sum(c.isalpha() for c in coach_name) > 5:
                        coaches.append({
                            'role': 'HEAD COACH' if 'head' in line.lower() else 'ASSISTANT COACH',
                            'name': coach_name.upper()
                        })
                continue
            
            # Skip other headers
            if any(x in line.lower() for x in ['goalie', 'skater', 'chairman', 'president', 'manager', 'general']):
                continue
            
            # If in coaching section, try to extract coach names
            if in_coaching_section:
                parts = [p for p in line.split() if sum(c.isalpha() for c in p) > 2]
                if len(parts) >= 2:
                    coach_name = ' '.join(parts[-2:]) if len(parts) >= 2 else parts[-1]
                    if sum(c.isalpha() for c in coach_name) > 5:
                        coaches.append({
                            'role': 'COACH',
                            'name': coach_name.upper()
                        })
                continue
            
            # Player extraction
            parts = line.split()
            
            if len(parts) < 3:
                continue
            
            if not parts[0].isdigit():
                continue
            
            number = parts[0]
            
            position = None
            name_start_idx = 1
            
            if len(parts[1]) == 1 and parts[1] in ['C', 'L', 'R', 'D', 'W']:
                position = 'F' if parts[1] in ['C', 'L', 'R', 'W'] else 'D'
                name_start_idx = 2
            
            if len(parts) > name_start_idx:
                name_parts = []
                for i in range(name_start_idx, len(parts)):
                    word = parts[i]
                    if word.isdigit() and len(word) <= 3:
                        break
                    if sum(c.isalpha() for c in word) >= len(word) * 0.5:
                        name_parts.append(word)
                
                if len(name_parts) >= 2:
                    full_name = ' '.join(name_parts).upper()
                    roster[number] = {
                        'name': full_name,
                        'position': position or 'F'
                    }
                    print(f"Found: #{number} - {full_name} ({position})")
        
        print(f"\nFound {len(coaches)} coaches: {[c['name'] for c in coaches]}")
        
        return roster, coaches
        
    except Exception as e:
        print(f"Roster OCR Error: {str(e)}")
        return {}, []

def extract_line_numbers(text=None, image_file=None):
    """Extract jersey numbers from text or screenshot"""
    numbers = []
    
    if text:
        text = re.sub(r'^\w+\s*\n', '', text)
        
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            line = line.replace('/', '-')
            
            found_numbers = re.findall(r'\d+', line)
            numbers.extend(found_numbers)
    
    elif image_file:
        try:
            image = Image.open(image_file)
            text = pytesseract.image_to_string(image, config='--psm 6')
            
            print("Line combinations OCR:")
            print(text)
            
            found_numbers = re.findall(r'\d+', text)
            numbers.extend(found_numbers)
            
        except Exception as e:
            print(f"Lines OCR Error: {str(e)}")
    
    return numbers

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
        # Check if using combined upload or separate
        combined_file = request.files.get('combined')
        forwards_file = request.files.get('forwards')
        defense_file = request.files.get('defense')
        
        if not combined_file and not (forwards_file and defense_file):
            return jsonify({'error': 'Screenshots required'}), 400
        
        # Detect team
        print("Identifying team...")
        temp_names = []
        
        if combined_file:
            combined_file.seek(0)
            image = Image.open(combined_file)
            text = pytesseract.image_to_string(image, config='--psm 6')
            
            print("Combined image OCR sample:")
            print(text[:500])
            
            # Skip header lines (first few lines often have opponent team name)
            lines = text.split('\n')
            skip_first_lines = 5  # Skip first 5 lines to avoid "at TORONTO" etc
            
            for line in lines[skip_first_lines:]:
                alpha = sum(1 for c in line if c.isalpha())
                if alpha > 20:
                    words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                    if len(words) >= 4:
                        name = f"{words[0]} {words[1]}"
                        temp_names.append(name)
                        if len(temp_names) >= 5:  # Get more names for better accuracy
                            break
        else:
            for img in [forwards_file, defense_file]:
                img.seek(0)
                image = Image.open(img)
                text = pytesseract.image_to_string(image, config='--psm 6')
                
                lines = text.split('\n')
                skip_first_lines = 3  # Skip first 3 lines
                
                for line in lines[skip_first_lines:]:
                    alpha = sum(1 for c in line if c.isalpha())
                    if alpha > 20:
                        words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                        if len(words) >= 4:
                            name = f"{words[0]} {words[1]}"
                            temp_names.append(name)
                            if len(temp_names) >= 5:
                                break
                if len(temp_names) >= 5:
                    break
        
        found_teams = []
        for name in temp_names[:8]:  # Check up to 8 names
            result = search_player(name)
            if result and result['team']:
                found_teams.append(result['team'])
                print(f"  {name} -> {result['team']}")
                time.sleep(0.2)
        
        # Use most common team (ignore single matches)
        if found_teams:
            team_counts = Counter(found_teams)
            default_team = team_counts.most_common(1)[0][0]
            print(f"\nDetected team: {default_team} (from {team_counts})")
        else:
            default_team = 'SJS'  # Fallback
            print(f"Could not detect team, using fallback: {default_team}")
        
        # Get roster
        roster_url = f"https://api-web.nhle.com/v1/roster/{default_team}/current"
        headers = {'User-Agent': 'Mozilla/5.0'}
        roster_response = requests.get(roster_url, headers=headers, timeout=10)
        
        roster_data = {}
        goalies_list = []
        
        if roster_response.status_code == 200:
            roster_json = roster_response.json()
            
            for position_group in ['forwards', 'defensemen']:
                for player in roster_json.get(position_group, []):
                    first = player.get('firstName', {}).get('default', '')
                    last = player.get('lastName', {}).get('default', '')
                    full_name = f"{first} {last}".strip().upper()
                    
                    roster_data[full_name] = {
                        'id': player.get('id'),
                        'number': str(player.get('sweaterNumber', '')),
                        'is_forward': position_group == 'forwards'
                    }
            
            for player in roster_json.get('goalies', [])[:2]:
                first = player.get('firstName', {}).get('default', '')
                last = player.get('lastName', {}).get('default', '')
                player_id = player.get('id')
                number = str(player.get('sweaterNumber', ''))
                display_name = f"#{number} {last.upper()}" if number and last else f"{first} {last}".upper()
                
                goalies_list.append({
                    'name': display_name,
                    'id': player_id,
                    'number': number,
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{default_team}/{player_id}.png"
                })
        
        print(f"Got roster data for {len(roster_data)} players and {len(goalies_list)} goalies")
        
        # Get coaches
        print(f"Attempting to fetch coaches for team: {default_team}")
        coaches_list = get_coaches_from_nhl(default_team)
        print(f"Got {len(coaches_list)} coaches")
        
        roster_forwards = [name for name, data in roster_data.items() if data['is_forward']]
        roster_defense = [name for name, data in roster_data.items() if not data['is_forward']]
        
        # Extract players
        if combined_file:
            combined_file.seek(0)
            forwards, defensemen = extract_players_from_combined_image(combined_file, roster_forwards, roster_defense)
        else:
            forwards_file.seek(0)
            defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, roster_forwards)
            defensemen = extract_players_from_image(defense_file, 6, roster_defense)
        
        print(f"Final forwards: {forwards}")
        print(f"Final defense: {defensemen}")
        
        # Build player data
        all_players = []
        
        for player_name in forwards + defensemen:
            if player_name in roster_data:
                player_info = roster_data[player_name]
                all_players.append({
                    'name': player_name,
                    'id': player_info['id'],
                    'team': default_team,
                    'number': player_info['number'],
                    'is_forward': player_name in forwards,
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{default_team}/{player_info['id']}.png"
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
        
        return jsonify({
            'forwards': [p for p in all_players if p['is_forward']],
            'defensemen': [p for p in all_players if not p['is_forward']],
            'goalies': goalies_list,
            'coaches': coaches_list,
            'team': default_team
        })
        
    except Exception as e:
        print(f"Process error: {str(e)}")
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
        
        if not team:
            return jsonify({'error': 'Team is required'}), 400
        
        if not roster_screenshot:
            return jsonify({'error': 'Roster screenshot is required'}), 400
        
        print(f"\n{'='*70}")
        print(f"Processing number-based lineup for {team}")
        print('='*70)
        
        roster_screenshot.seek(0)
        roster, coaches = extract_roster_from_screenshot(roster_screenshot)
        
        if not roster:
            return jsonify({'error': 'Could not extract roster from screenshot'}), 400
        
        print(f"\nExtracted {len(roster)} players from roster")
        
        if lines_screenshot:
            lines_screenshot.seek(0)
            jersey_numbers = extract_line_numbers(image_file=lines_screenshot)
        elif lines_text:
            jersey_numbers = extract_line_numbers(text=lines_text)
        else:
            return jsonify({'error': 'Line combinations required'}), 400
        
        print(f"\nExtracted jersey numbers: {jersey_numbers}")
        
        roster_url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        headers = {'User-Agent': 'Mozilla/5.0'}
        roster_response = requests.get(roster_url, headers=headers, timeout=10)
        
        api_roster = {}
        goalies_list = []
        
        if roster_response.status_code == 200:
            roster_json = roster_response.json()
            
            for position_group in ['forwards', 'defensemen']:
                for player in roster_json.get(position_group, []):
                    number = str(player.get('sweaterNumber', ''))
                    first = player.get('firstName', {}).get('default', '')
                    last = player.get('lastName', {}).get('default', '')
                    full_name = f"{first} {last}".strip().upper()
                    
                    api_roster[number] = {
                        'name': full_name,
                        'id': player.get('id'),
                        'position': 'F' if position_group == 'forwards' else 'D'
                    }
            
            for player in roster_json.get('goalies', [])[:2]:
                first = player.get('firstName', {}).get('default', '')
                last = player.get('lastName', {}).get('default', '')
                player_id = player.get('id')
                number = str(player.get('sweaterNumber', ''))
                display_name = f"#{number} {last.upper()}" if number and last else f"{first} {last}".upper()
                
                goalies_list.append({
                    'name': display_name,
                    'id': player_id,
                    'number': number,
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{player_id}.png"
                })
        
        # Get coaches from NHL.com
        print(f"Attempting to fetch coaches for team: {team}")
        coaches_from_nhl = get_coaches_from_nhl(team)
        
        # Use NHL.com coaches if available, otherwise use OCR coaches
        final_coaches = coaches_from_nhl if coaches_from_nhl else coaches
        print(f"Using {len(final_coaches)} coaches")
        
        all_players = []
        
        for i, number in enumerate(jersey_numbers[:18]):
            is_forward = i < 12
            
            player_name = None
            player_id = None
            
            if number in roster:
                player_name = roster[number]['name']
                print(f"#{number} matched to {player_name} from OCR roster")
            
            if number in api_roster:
                api_name = api_roster[number]['name']
                player_id = api_roster[number]['id']
                
                if not player_name:
                    player_name = api_name
                    print(f"#{number} matched to {player_name} from API roster")
                else:
                    print(f"#{number} using API ID for {player_name}")
            
            if player_name:
                all_players.append({
                    'name': player_name,
                    'id': player_id,
                    'team': team,
                    'number': number,
                    'is_forward': is_forward,
                    'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{player_id}.png" if player_id else None
                })
            else:
                all_players.append({
                    'name': f"PLAYER #{number}",
                    'id': None,
                    'team': team,
                    'number': number,
                    'is_forward': is_forward,
                    'headshot_url': None
                })
        
        forwards = [p for p in all_players if p['is_forward']]
        defensemen = [p for p in all_players if not p['is_forward']]
        
        while len(forwards) < 12:
            forwards.append({
                'name': f"PLAYER {len(forwards)+1}",
                'id': None,
                'team': team,
                'number': '',
                'is_forward': True,
                'headshot_url': None
            })
        
        while len(defensemen) < 6:
            defensemen.append({
                'name': f"PLAYER {len(defensemen)+1}",
                'id': None,
                'team': team,
                'number': '',
                'is_forward': False,
                'headshot_url': None
            })
        
        print(f"\nFinal: {len(forwards)} forwards, {len(defensemen)} defense, {len(goalies_list)} goalies, {len(final_coaches)} coaches")
        
        return jsonify({
            'forwards': forwards[:12],
            'defensemen': defensemen[:6],
            'goalies': goalies_list,
            'coaches': final_coaches[:3],
            'team': team
        })
        
    except Exception as e:
        print(f"Process error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
