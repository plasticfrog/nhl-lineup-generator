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

def get_grid_sorted_words(image):
    """
    Groups words into logical clusters (Jerseys) and sorts them 
    Top-to-Bottom, then Left-to-Right.
    """
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
    words = []
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        if text and len(text) > 1:
            words.append({
                'text': text,
                'left': data['left'][i],
                'top': data['top'][i],
                'width': data['width'][i],
                'height': data['height'][i],
                'center_x': data['left'][i] + (data['width'][i] / 2),
                'center_y': data['top'][i] + (data['height'][i] / 2)
            })
    
    if not words: return []

    # 1. Determine Row Height (use average word height)
    avg_h = sum(w['height'] for w in words) / len(words)
    row_threshold = avg_h * 4 # Large threshold to group names and jersey numbers together

    # 2. Cluster words into rows based on Y-coordinate
    words.sort(key=lambda x: x['center_y'])
    rows = []
    if words:
        current_row = [words[0]]
        for i in range(1, len(words)):
            if abs(words[i]['center_y'] - current_row[0]['center_y']) < row_threshold:
                current_row.append(words[i])
            else:
                rows.append(current_row)
                current_row = [words[i]]
        rows.append(current_row)

    # 3. Within each row, cluster words horizontally into "Jersey Blocks"
    ordered_names = []
    for row in rows:
        row.sort(key=lambda x: x['left'])
        jersey_blocks = []
        if row:
            current_block = [row[0]]
            for i in range(1, len(row)):
                # If gap is small (less than 3 word widths), it's the same jersey
                gap = row[i]['left'] - (current_block[-1]['left'] + current_block[-1]['width'])
                if gap < (avg_h * 5): 
                    current_block.append(row[i])
                else:
                    jersey_blocks.append(current_block)
                    current_block = [row[i]]
            jersey_blocks.append(current_block)

        for block in jersey_blocks:
            block_text = " ".join([w['text'] for w in block])
            ordered_names.append(block_text)
            print(f"[DEBUG] Jersey Block Identified: {block_text}", flush=True)

    return ordered_names

def match_name_to_roster(ocr_name, roster_list, used_names, is_forward_context=None, roster_data_full=None):
    """Improved matcher that handles duplicate last names (Petterssons) using position context"""
    ocr_name = ocr_name.replace('3', 'S').replace('0', 'O').replace('1', 'I').replace('4', 'A').replace('5', 'S').upper()
    
    best_match = None
    best_score = 0
    
    for roster_name in roster_list:
        if roster_name in used_names: continue
        
        # If we know we are looking for a forward, skip defensemen with same last name
        if is_forward_context is not None and roster_data_full:
            player_info = roster_data_full.get(roster_name)
            if player_info and player_info['is_forward'] != is_forward_context:
                continue

        roster_parts = roster_name.split()
        score = 0
        
        # Primary check: Last Name match
        last_name = roster_parts[-1]
        if last_name in ocr_name:
            score += 100
            
            # Tie-breaker: First Name match (crucial for Petterssons)
            first_name = roster_parts[0]
            if first_name in ocr_name:
                score += 50
            elif len(first_name) > 3 and first_name[:3] in ocr_name:
                score += 25
        
        if score > best_score:
            best_score = score
            best_match = roster_name
    
    return best_match if best_score >= 80 else None

def extract_players_from_image(image_file, expected_count, team_roster, type_label, is_forward, roster_data_full):
    """Processes images using Grid Sorting to ensure Left-to-Right order"""
    try:
        print(f"\n=== PROCESSING {type_label} IMAGE ===", flush=True)
        image = Image.open(image_file)
        blocks = get_grid_sorted_words(image)
        
        matched_names = []
        used_roster = set()
        
        for block_text in blocks:
            match = match_name_to_roster(block_text, team_roster, used_roster, is_forward, roster_data_full)
            if match:
                print(f"[{type_label} MATCH] {match}", flush=True)
                matched_names.append(match)
                used_roster.add(match)
            if len(matched_names) >= expected_count: break
        
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        return matched_names[:expected_count]
    except Exception as e:
        print(f"[ERROR] {type_label} extraction failed: {str(e)}", flush=True)
        return [f"PLAYER {i+1}" for i in range(expected_count)]

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense, roster_goalies, roster_data_full):
    """Processes combined images with Grid Sorting"""
    try:
        print("\n=== PROCESSING COMBINED IMAGE ===", flush=True)
        image = Image.open(image_file)
        blocks = get_grid_sorted_words(image)
        
        extracted_skaters = []
        used_skaters = set()
        used_goalies = set()
        
        all_skater_names = roster_forwards + roster_defense

        for block_text in blocks:
            # Check Goalies first to filter them out
            g_match = match_name_to_roster(block_text, roster_goalies, used_goalies)
            if g_match:
                print(f"[GOALIE FILTER] {g_match}", flush=True)
                used_goalies.add(g_match)
                continue
                
            # Check Skaters (logic will handle 12 forwards then 6 defense)
            # We don't pass is_forward_context here because combined order can vary
            s_match = match_name_to_roster(block_text, all_skater_names, used_skaters)
            if s_match:
                print(f"[SKATER MATCH] {s_match}", flush=True)
                extracted_skaters.append(s_match)
                used_skaters.add(s_match)

        forwards = extracted_skaters[:12]
        defense = extracted_skaters[12:18]
        while len(forwards) < 12: forwards.append(f"PLAYER {len(forwards)+1}")
        while len(defense) < 6: defense.append(f"PLAYER {len(defense)+1}")
        return forwards, defense
    except Exception as e:
        print(f"[ERROR] Combined failed: {str(e)}", flush=True)
        return [f"PLAYER {i+1}" for i in range(12)], [f"PLAYER {i+1}" for i in range(6)]

def search_player(player_name):
    if not player_name or player_name.startswith("PLAYER "): return None
    try:
        search_url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q={player_name.replace(' ', '%20')}"
        response = requests.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data: return {'id': data[0].get('playerId'), 'team': data[0].get('teamAbbrev'), 'full_name': data[0].get('name')}
    except: pass
    return None

def get_coaches_from_nhl(team_abbrev):
    try:
        team_slug = TEAM_NAME_MAP.get(team_abbrev, team_abbrev.lower())
        response = requests.get(f"https://www.nhl.com/{team_slug}/team/coaches", headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            coaches = []
            for img in soup.find_all('img'):
                alt = img.get('alt', '').upper()
                if any(k in alt for k in ['COACH', 'ASSISTANT']):
                    name = alt.split('-')[0].strip()
                    coaches.append({'name': name, 'role': 'COACH', 'headshot_url': img.get('src')})
                    if len(coaches) >= 4: break
            return coaches
    except: pass
    return []

@app.route('/')
def index(): return render_template('index.html')

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
        image = Image.open(sample)
        blocks = get_grid_sorted_words(image)
        
        found_teams = []
        for b in blocks[:10]:
            res = search_player(b)
            if res and res['team']: found_teams.append(res['team'])
        
        default_team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        print(f"DETECTED TEAM: {default_team}", flush=True)
        
        r_json = requests.get(f"https://api-web.nhle.com/v1/roster/{default_team}/current").json()
        roster_data_full = {}
        goalies_list = []
        
        for pos in ['forwards', 'defensemen', 'goalies']:
            for p in r_json.get(pos, []):
                name = f"{p['firstName']['default']} {p['lastName']['default']}".upper()
                if pos == 'goalies':
                    goalies_list.append({'name': f"#{p['sweaterNumber']} {p['lastName']['default'].upper()}", 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{default_team}/{p['id']}.png"})
                else:
                    roster_data_full[name] = {'id': p['id'], 'number': str(p['sweaterNumber']), 'is_forward': pos=='forwards'}

        roster_f_names = [n for n, d in roster_data_full.items() if d['is_forward']]
        roster_d_names = [n for n, d in roster_data_full.items() if not d['is_forward']]
        roster_g_names = [g['name'].split()[-1] for g in goalies_list]

        if combined_file:
            combined_file.seek(0)
            forwards, defense = extract_players_from_combined_image(combined_file, roster_f_names, roster_d_names, roster_g_names, roster_data_full)
        else:
            forwards_file.seek(0); defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, roster_f_names, "FORWARDS", True, roster_data_full)
            defense = extract_players_from_image(defense_file, 6, roster_d_names, "DEFENSE", False, roster_data_full)

        final_players = []
        for name in forwards + defense:
            info = roster_data_full.get(name, {'id': None, 'number': ''})
            final_players.append({
                'name': name, 'number': info['number'], 'is_forward': name in forwards,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20242025/{default_team}/{info['id']}.png" if info['id'] else None
            })

        return jsonify({
            'forwards': [p for p in final_players if p['is_forward']],
            'defensemen': [p for p in final_players if not p['is_forward']],
            'goalies': goalies_list[:2],
            'coaches': get_coaches_from_nhl(default_team),
            'team': default_team
        })
    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}", flush=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
