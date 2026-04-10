import requests
from bs4 import BeautifulSoup
import re

MLB_TEAMS = {
    'angels':      {'id': 108, 'name': 'Angels',      'colors': {'primary': '#BA0021', 'secondary': '#003263'}},
    'astros':      {'id': 117, 'name': 'Astros',      'colors': {'primary': '#002D62', 'secondary': '#EB6E1F'}},
    'athletics':   {'id': 133, 'name': 'Athletics',    'colors': {'primary': '#003831', 'secondary': '#EFB21E'}},
    'bluejays':    {'id': 141, 'name': 'Blue Jays',    'colors': {'primary': '#134A8E', 'secondary': '#1D2D5C'}},
    'braves':      {'id': 144, 'name': 'Braves',      'colors': {'primary': '#CE1141', 'secondary': '#13274F'}},
    'brewers':     {'id': 158, 'name': 'Brewers',     'colors': {'primary': '#12284B', 'secondary': '#FFC52F'}},
    'cardinals':   {'id': 138, 'name': 'Cardinals',    'colors': {'primary': '#C41E3A', 'secondary': '#0C2340'}},
    'cubs':        {'id': 112, 'name': 'Cubs',         'colors': {'primary': '#0E3386', 'secondary': '#CC3433'}},
    'dbacks':      {'id': 109, 'name': 'D-backs',     'colors': {'primary': '#A71930', 'secondary': '#E3D4AD'}},
    'dodgers':     {'id': 119, 'name': 'Dodgers',     'colors': {'primary': '#005A9C', 'secondary': '#A5ACAF'}},
    'giants':      {'id': 137, 'name': 'Giants',      'colors': {'primary': '#FD5A1E', 'secondary': '#27251F'}},
    'guardians':   {'id': 114, 'name': 'Guardians',   'colors': {'primary': '#00385D', 'secondary': '#E50022'}},
    'mariners':    {'id': 136, 'name': 'Mariners',    'colors': {'primary': '#0C2C56', 'secondary': '#005C5C'}},
    'marlins':     {'id': 146, 'name': 'Marlins',     'colors': {'primary': '#00A3E0', 'secondary': '#EF3340'}},
    'mets':        {'id': 121, 'name': 'Mets',         'colors': {'primary': '#002D72', 'secondary': '#FF5910'}},
    'nationals':   {'id': 120, 'name': 'Nationals',    'colors': {'primary': '#AB0003', 'secondary': '#14225A'}},
    'orioles':     {'id': 110, 'name': 'Orioles',     'colors': {'primary': '#DF4601', 'secondary': '#27251F'}},
    'padres':      {'id': 135, 'name': 'Padres',      'colors': {'primary': '#2F241D', 'secondary': '#FFC425'}},
    'phillies':    {'id': 143, 'name': 'Phillies',    'colors': {'primary': '#E81828', 'secondary': '#002D72'}},
    'pirates':     {'id': 134, 'name': 'Pirates',     'colors': {'primary': '#27251F', 'secondary': '#FDB827'}},
    'rangers':     {'id': 140, 'name': 'Rangers',     'colors': {'primary': '#003278', 'secondary': '#C0111F'}},
    'rays':        {'id': 139, 'name': 'Rays',         'colors': {'primary': '#092C5C', 'secondary': '#8FBCE6'}},
    'reds':        {'id': 113, 'name': 'Reds',         'colors': {'primary': '#C6011F', 'secondary': '#000000'}},
    'redsox':      {'id': 111, 'name': 'Red Sox',     'colors': {'primary': '#BD3039', 'secondary': '#0C2340'}},
    'rockies':     {'id': 115, 'name': 'Rockies',     'colors': {'primary': '#33006F', 'secondary': '#C4CED4'}},
    'royals':      {'id': 118, 'name': 'Royals',      'colors': {'primary': '#004687', 'secondary': '#BD9B60'}},
    'tigers':      {'id': 116, 'name': 'Tigers',      'colors': {'primary': '#0C2340', 'secondary': '#FA4616'}},
    'twins':       {'id': 142, 'name': 'Twins',       'colors': {'primary': '#002B5C', 'secondary': '#D31145'}},
    'whitesox':    {'id': 145, 'name': 'White Sox',   'colors': {'primary': '#27251F', 'secondary': '#C4CED4'}},
    'yankees':     {'id': 147, 'name': 'Yankees',     'colors': {'primary': '#003087', 'secondary': '#C4CED4'}},
}

def fetch_team_data(team_slug, team_id):
    colors = MLB_TEAMS.get(team_slug, {}).get('colors', {'primary': '#333333', 'secondary': '#666666'})

    # 1. Scrape Coaches
    coaches = []
    try:
        url = f"https://www.mlb.com/{team_slug}/roster/coaches"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')

        coach_imgs = soup.find_all('img', src=re.compile(r'headshot/(\d+)/coach'))
        seen_ids = set()
        for img_tag in coach_imgs:
            src = img_tag.get('src') or img_tag.get('data-src') or ""
            name = img_tag.get('alt', '').upper()
            id_match = re.search(r'people/(\d+)', src)
            if id_match:
                p_id = id_match.group(1)
                if p_id in seen_ids:
                    continue
                role = "COACH"
                parent_td = img_tag.find_parent('td')
                if parent_td:
                    info_td = parent_td.find_previous_sibling('td', class_='info') or parent_td.find_next_sibling('td', class_='info')
                    if info_td:
                        role_tag = info_td.find('span', class_='mobile-info__position')
                        if role_tag:
                            role = role_tag.text.strip().upper().replace(" COACH", "").replace("COACH ", "").strip()
                img_final = f"https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:83:current.png/ar_1:1,c_pad,b_auto:border/r_max/w_640,q_auto:best/v1/people/{p_id}/headshot/83/coach/current"
                coaches.append({'name': name, 'role': role, 'image': img_final})
                seen_ids.add(p_id)
            if len(coaches) >= 12:
                break
    except Exception as e:
        print(f"  Coach Scrape Error: {e}")

    # 2. Fetch Players + Pitching Stats
    pitchers, infield, outfield, catchers = [], [], [], []
    try:
        api_url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/Active?hydrate=person(batSide,pitchHand,stats(type=season,season=2026,group=pitching))"
        data = requests.get(api_url, timeout=10).json()
        seen_names = set()
        for p in data.get('roster', []):
            person = p['person']
            pos = p['position']['abbreviation']
            name = person['fullName'].upper()
            if name in seen_names:
                continue
            seen_names.add(name)
            bats = person.get('batSide', {}).get('code', 'R')
            throws = person.get('pitchHand', {}).get('code', 'R')
            starts, saves = 0, 0
            if 'stats' in person:
                stats_list = person['stats'][0].get('splits', [])
                if stats_list:
                    starts = stats_list[0].get('stat', {}).get('gamesStarted', 0)
                    saves = stats_list[0].get('stat', {}).get('saves', 0)
            player_obj = {
                'name': name,
                'number': p.get('jerseyNumber', '00'),
                'throw_l': "(L)" if throws == 'L' else "",
                'bat_l': "(L)" if bats in ['L', 'S'] else "",
                'image': f"https://img.mlbstatic.com/mlb-photos/image/upload/w_640,q_auto:best/v1/people/{person['id']}/headshot/silo/current",
                'starts': starts,
                'saves': saves
            }
            if pos == 'P':
                pitchers.append(player_obj)
            elif pos in ['1B', '2B', '3B', 'SS', 'IF', 'DH', 'TWP']:
                infield.append(player_obj)
            elif pos in ['LF', 'CF', 'RF', 'OF']:
                outfield.append(player_obj)
            elif pos == 'C':
                catchers.append(player_obj)
    except Exception as e:
        print(f"  API Error: {e}")

    def by_last_name(p):
        parts = p['name'].split()
        return parts[-1] if parts else ''

    # Closer = most saves from 2025 season
    p_by_saves = sorted(pitchers, key=lambda x: x['saves'], reverse=True)
    closer = p_by_saves[0] if p_by_saves and p_by_saves[0]['saves'] > 0 else None
    if closer:
        closer['is_closer'] = True

    # Top 5 starters by games started (excluding closer), sorted alphabetically
    non_closer = [p for p in pitchers if p != closer]
    p_by_starts = sorted(non_closer, key=lambda x: x['starts'], reverse=True)
    starters = sorted(p_by_starts[:5], key=by_last_name)

    # Relievers = everyone else (excluding closer and starters), sorted alphabetically
    starter_set = set(id(p) for p in starters)
    relievers = sorted([p for p in non_closer if id(p) not in starter_set], key=by_last_name)

    # Insert closer alphabetically within the relievers
    if closer:
        inserted = False
        for i, p in enumerate(relievers):
            if by_last_name(closer) <= by_last_name(p):
                relievers.insert(i, closer)
                inserted = True
                break
        if not inserted:
            relievers.append(closer)

    all_pitchers = (starters + relievers)[:13]
    while len(all_pitchers) < 13:
        all_pitchers.append({'name': '', 'number': '', 'throw_l': '', 'image': ''})

    # Sort each position group alphabetically by last name
    infield.sort(key=by_last_name)
    outfield.sort(key=by_last_name)
    catchers.sort(key=by_last_name)

    all_pos = (infield + outfield + catchers)[:13]
    # Calculate actual displayed counts (after 13-cap)
    shown_if = min(len(infield), 13)
    shown_of = min(len(outfield), 13 - shown_if)
    shown_c = min(len(catchers), 13 - shown_if - shown_of)
    while len(all_pos) < 13:
        all_pos.append({'name': '', 'number': '', 'bat_l': '', 'image': ''})

    return {
        'id': team_id,
        'name': team_slug.upper(),
        'colors': colors,
        'logo': f"https://www.mlbstatic.com/team-logos/team-cap-on-light/{team_id}.svg",
        'coaches': coaches,
        'pitchers': all_pitchers,
        'pos_players': all_pos,
        'pos_counts': {'infield': shown_if, 'outfield': shown_of, 'catchers': shown_c}
    }
