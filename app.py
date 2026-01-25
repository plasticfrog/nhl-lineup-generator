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

def extract_via_visual_grid(image_file, roster_subset, cols, rows):
    """Core logic: Divides image area into grid bins and matches text found in each bin"""
    try:
        img = Image.open(image_file)
        width, height = img.size
        # Get coordinates for every word found in the image
        d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        
        # Create visual bins for each player slot in the grid
        grid_bins = [["" for _ in range(cols)] for _ in range(rows)]
        
        for i in range(len(d['text'])):
            text = d['text'][i].strip()
            # Basic cleanup and confidence check
            if not text or int(d['conf'][i]) < 15: continue
            
            # Find which visual box this text belongs to
            cx = d['left'][i] + (d['width'][i] / 2)
            cy = d['top'][i] + (d['height'][i] / 2)
            
            col_idx = min(int(cx / (width / cols)), cols - 1)
            row_idx = min(int(cy / (height / rows)), rows - 1)
            grid_bins[row_idx][col_idx] += " " + text

        final_names = []
        used_names = set()
        
        # Process bins in order (Row 1 Col 1, Row 1 Col 2...)
        for r in range(rows):
            for c in range(cols):
                raw_bin_text = grid_bins[r][c].strip().upper()
                # Clean OCR noise
                clean_text = raw_bin_text.replace('3', 'S').replace('5', 'S').replace('1', 'I')
                match = match_name_to_roster(clean_text, roster_subset, used_names)
                if match:
                    final_names.append(match)
                    used_names.add(match)
                else:
                    # Fallback to cleaned text if no match, or generic placeholder
                    fallback = re.sub(r'[^A-Z\s]', '', clean_text).strip()
                    final_names.append(fallback if len(fallback) > 3 else f"PLAYER {len(final_names)+1}")
        return final_names
    except:
        return [f"PLAYER {i+1}" for i in range(cols * rows)]

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense):
    """Extract players from single image maintain 3-column grid order"""
    all_players = extract_via_visual_grid(image_file, roster_forwards + roster_defense, 3, 6)
    return all_players[:12], all_players[12:18]

def extract_players_from_image(image_file, expected_count, team_roster):
    """Extract player names from separate grid image (3x4 for forwards, 2x3 for defense)"""
    if expected_count == 12:
        return extract_via_visual_grid(image_file, team_roster, 3, 4)
    else:
        return extract_via_visual_grid(image_file, team_roster, 2, 3)

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
                    coaches_list.append({
                        'name': name_match.upper(),
                        'role': role,
                        'headshot_url': src
                    })
                    if len(coaches_list) >= 3:
                        break
        return coaches_list[:3]
    except Exception as e:
        return []

def extract_roster_from_screenshot(image_file):
    """Extract player roster from stats table screenshot"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        lines = text.split('\n')
        roster = {}
        coaches = []
        in_coaching_section = False
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if any(x in line.lower() for x in ['head coach', 'assistant coach', 'coach']):
                in_coaching_section = True
                parts = line.split()
                if len(parts) >= 3:
                    coach_name = ' '.join(parts[-2:])
                    if sum(c.isalpha() for c in coach_name) > 5:
                        coaches.append({
                            'role': 'HEAD COACH' if 'head' in line.lower() else 'ASSISTANT COACH',
                            'name': coach_name.upper()
                        })
                continue
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
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit():
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
        return roster, coaches
    except:
        return {}, []

def extract_line_numbers(text=None, image_file=None):
    """Extract jersey numbers from text or screenshot"""
    numbers = []
    if text:
        text = re.sub(r'^\w+\s*\n', '', text)
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if not line: continue
            line = line.replace('/', '-')
            found_numbers = re.findall(r'\d+', line)
            numbers.extend(found_numbers)
    elif image_file:
        try:
            image = Image.open(image_file)
            text = pytesseract.image_to_string(image, c
