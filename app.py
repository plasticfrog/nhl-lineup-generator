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
    
    # Clean OCR noise
    ocr_name = ocr_name.upper().replace('3', 'S').replace('5', 'S').replace('1', 'I')
    ocr_parts = re.sub(r'[^A-Z\s]', '', ocr_name).split()
    
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
        
        if len(ocr_parts) > 1 and len(roster_parts) > 1:
            if ocr_parts[0] == roster_parts[0]:
                score += 50
        
        if score > best_score:
            best_score = score
            best_match = roster_name
    
    return best_match if best_score >= 50 else None

def extract_via_grid(image_file, roster_subset, cols, rows):
    """
    Uses visual clustering to group words into player 'cards'.
    Ignores image size/resolution and focuses on text proximity.
    """
    try:
        img = Image.open(image_file)
        # Get data with coordinates
        d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        
        # 1. Collect all valid text fragments with their locations
        fragments = []
        for i in range(len(d['text'])):
            text = d['text'][i].strip()
            if text and int(d['conf'][i]) > 20:
                # Ignore common stat labels that aren't names
                if text.upper() in ["GP", "G", "A", "P", "GAA", "SVP", "IGP", "PTS"]: continue
                fragments.append({
                    'text': text,
                    'cx': d['left'][i] + (d['width'][i] / 2),
                    'cy': d['top'][i] + (d['height'][i] / 2),
                    'top': d['top'][i],
                    'left': d['left'][i],
                    'height': d['height'][i]
                })
        
        if not fragments:
            return [f"PLAYER {i+1}" for i in range(cols * rows)]

        # 2. Cluster fragments that are physically close (belong to the same jersey/card)
        # We calculate threshold based on average text height to be resolution-independent
        avg_h = sum(f['height'] for f in fragments) / len(fragments)
        x_thresh = avg_h * 4  # Horizontal proximity
        y_thresh = avg_h * 3  # Vertical proximity

        clusters = []
        for f in fragments:
            added = False
            for cluster in clusters:
                # Check if this fragment is close to any fragment already in the cluster
                for member in cluster:
                    if abs(f['cx'] - member['cx']) < x_thresh and abs(f['cy'] - member['cy']) < y_thresh:
                        cluster.append(f)
                        added = True
                        break
                if added: break
            if not added:
                clusters.append([f])

        # 3. Create a combined text string for each cluster and find its center
        processed_clusters = []
        for cluster in clusters:
            combined_text = " ".join(f['text'] for f in cluster)
            avg_x = sum(f['cx'] for f in cluster) / len(cluster)
            avg_y = sum(f['cy'] for f in cluster) / len(cluster)
            processed_clusters.append({'text': combined_text, 'x': avg_x, 'y': avg_y})

        # 4. Sort clusters: Top-to-Bottom (Rows), then Left-to-Right (Columns)
        # We use a slight tolerance for Y to group items on the same "row"
        row_tolerance = avg_h * 2
        processed_clusters.sort(key=lambda c: c['y'])
        
        final_sorted = []
        while processed_clusters:
            # Take the top-most cluster and find all others in the same row
            base_y = processed_clusters[0]['y']
            row = [c for c in processed_clusters if abs(c['y'] - base_y) < row_tolerance]
            # Sort the row left-to-right
            row.sort(key=lambda c: c['x'])
            final_sorted.extend(row)
            # Remove processed row
            processed_clusters = [c for c in processed_clusters if c not in row]

        # 5. Match sorted clusters to the roster
        final_names = []
        used_names = set()
        for cluster in final_sorted:
            match = match_name_to_roster(cluster['text'], roster_subset, used_names)
            if match:
                final_names.append(match)
                used_names.add(match)
            else:
                clean = re.sub(r'[^A-Z\s]', '', cluster['text'].upper()).strip()
                if len(clean) > 3:
                    final_names.append(clean)

        # Pad or truncate to match expected count
        while len(final_names) < (cols * rows):
            final_names.append(f"PLAYER {len(final_names)+1}")
            
        return final_names[:cols * rows]

    except Exception as e:
        print(f"Visual Cluster Error: {e}")
        return [f"PLAYER {i+1}" for i in range(cols * rows)]

def extract_players_from_combined_image(image_file, roster_forwards, roster_defense):
    all_players = extract_via_grid(image_file, roster_forwards + roster_defense, 3, 6)
    return all_players[:12], all_players[12:18]

def extract_players_from_image(image_file, expected_count, team_roster):
    if expected_count == 12:
        return extract_via_grid(image_file, team_roster, 3, 4)
    else:
        return extract_via_grid(image_file, team_roster, 2, 3)

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
        roster, coaches, in_coaching = {}, [], False
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5: continue
            if any(x in line.lower() for x in ['head coach', 'assistant coach', 'coach']):
                in_coaching = True
                parts = line.split()
                if len(parts) >= 3:
                    coaches.append({'role': 'HEAD COACH' if 'head' in line.lower() else 'ASSISTANT COACH', 'name': ' '.join(parts[-2:]).upper()})
                continue
            if in_coaching:
                parts = [p for p in line.split() if sum(c.isalpha() for c in p) > 2]
                if len(parts) >= 2: coaches.append({'role': 'COACH', 'name': ' '.join(parts[-2:]).upper()})
                continue
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit(): continue
            num = parts[0]
            pos = 'F'
            idx = 1
            if len(parts[1]) == 1 and parts[1] in ['C', 'L', 'R', 'D', 'W']:
                pos = 'F' if parts[1] in ['C', 'L', 'R', 'W'] else 'D'
                idx = 2
            name = []
            for i in range(idx, len(parts)):
                if parts[i].isdigit(): break
                name.append(parts[i])
            if len(name) >= 2: roster[num] = {'name': ' '.join(name).upper(), 'position': pos}
        return roster, coaches
    except: return {}, []

def extract_line_numbers(text=None, image_file=None):
    if text:
        text = re.sub(r'^\w+\s*\n', '', text)
        return re.findall(r'\d+', text.replace('/', '-'))
    elif image_file:
        try:
            return re.findall(r'\d+', pytesseract.image_to_string(Image.open(image_file), config='--psm 6'))
        except: pass
    return []

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
        if not combined_file and not (forwards_file and defense_file): return jsonify({'error': 'Screenshots required'}), 400
        
        temp_names = []
        sample = combined_file if combined_file else forwards_file
        sample.seek(0)
        text = pytesseract.image_to_string(Image.open(sample), config='--psm 6')
        lines = text.split('\n')
        for line in lines[5:]:
            if sum(1 for c in line if c.isalpha()) > 20:
                words = [w for w in line.split() if len(''.join(c for c in w if c.isalpha())) >= 3]
                if len(words) >= 4:
                    temp_names.append(f"{words[0]} {words[1]}")
                    if len(temp_names) >= 5: break
        
        found_teams = []
        for name in temp_names[:8]:
            res = search_player(name)
            if res and res['team']: found_teams.append(res['team'])
        
        team = Counter(found_teams).most_common(1)[0][0] if found_teams else 'SJS'
        roster_url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r_json = requests.get(roster_url, headers=headers, timeout=10).json()
        
        roster_data, goalies_list = {}, []
        for pg in ['forwards', 'defensemen']:
            for p in r_json.get(pg, []):
                full = f"{p['firstName']['default']} {p['lastName']['default']}".strip().upper()
                roster_data[full] = {'id': p['id'], 'num': str(p['sweaterNumber']), 'is_forward': pg == 'forwards'}
        
        for p in r_json.get('goalies', [])[:2]:
            num = str(p.get('sweaterNumber', ''))
            goalies_list.append({
                'name': f"#{num} {p['lastName']['default'].upper()}",
                'id': p['id'], 'num': num,
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{p['id']}.png"
            })
            
        r_f = [n for n, d in roster_data.items() if d['is_forward']]
        r_d = [n for n, d in roster_data.items() if not d['is_forward']]
        
        if combined_file:
            combined_file.seek(0)
            forwards, defensemen = extract_players_from_combined_image(combined_file, r_f, r_d)
        else:
            forwards_file.seek(0); defense_file.seek(0)
            forwards = extract_players_from_image(forwards_file, 12, r_f)
            defensemen = extract_players_from_image(defense_file, 6, r_d)
            
        all_p = []
        for n in forwards + defensemen:
            info = roster_data.get(n, {'id': None, 'num': '', 'is_forward': n in forwards})
            all_p.append({
                'name': n, 'id': info['id'], 'team': team, 'number': info['num'], 'is_forward': info['is_forward'],
                'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{info['id']}.png" if info['id'] else None
            })
            
        return jsonify({'forwards': [p for p in all_p if p['is_forward']], 'defensemen': [p for p in all_p if not p['is_forward']], 'goalies': goalies_list, 'coaches': get_coaches_from_nhl(team), 'team': team})
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
        if not roster: return jsonify({'error': 'OCR failed'}), 400
        if lines_screenshot:
            lines_screenshot.seek(0)
            j_nums = extract_line_numbers(image_file=lines_screenshot)
        else:
            j_nums = extract_line_numbers(text=lines_text)
        api_roster_json = requests.get(f"https://api-web.nhle.com/v1/roster/{team}/current", timeout=10).json()
        api_roster, goalies_list = {}, []
        for pg in ['forwards', 'defensemen']:
            for p in api_roster_json.get(pg, []):
                api_roster[str(p.get('sweaterNumber', ''))] = {'name': f"{p['firstName']['default']} {p['lastName']['default']}".upper(), 'id': p.get('id'), 'is_forward': pg == 'forwards'}
        for p in api_roster_json.get('goalies', [])[:2]:
            num = str(p.get('sweaterNumber', ''))
            goalies_list.append({'name': f"#{num} {p['lastName']['default'].upper()}", 'id': p.get('id'), 'num': num, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{p['id']}.png"})
        final_coaches = get_coaches_from_nhl(team) or coaches
        all_p = []
        for i, num in enumerate(j_nums[:18]):
            p_api = api_roster.get(num)
            name = p_api['name'] if p_api else (roster.get(num, {}).get('name') or f"PLAYER #{num}")
            all_p.append({'name': name, 'id': p_api['id'] if p_api else None, 'team': team, 'number': num, 'is_forward': i < 12, 'headshot_url': f"https://assets.nhle.com/mugs/nhl/20252026/{team}/{p_api['id']}.png" if p_api else None})
        return jsonify({'forwards': [p for p in all_p if p['is_forward']], 'defensemen': [p for p in all_p if not p['is_forward']], 'goalies': goalies_list, 'coaches': final_coaches[:3], 'team': team})
    except Exception as e: return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
