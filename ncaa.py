"""NCAA basketball (men's and women's): rosters and headshots from ESPN, coaching staffs (with photos)
from each school's athletics site, which run on a few common platforms (SIDEARM, WMT)."""
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin

import requests

logger = logging.getLogger(__name__)

# ESPN team id: (school, nickname, color, conference, athletics site)
NCAA_TEAMS = {
    '103': ('Boston College', 'Eagles', '#8C2232', 'acc', 'bceagles.com'),
    '25': ('California', 'Golden Bears', '#041E42', 'acc', 'calbears.com'),
    '228': ('Clemson', 'Tigers', '#F56600', 'acc', 'clemsontigers.com'),
    '150': ('Duke', 'Blue Devils', '#00539B', 'acc', 'goduke.com'),
    '52': ('Florida State', 'Seminoles', '#782F40', 'acc', 'seminoles.com'),
    '59': ('Georgia Tech', 'Yellow Jackets', '#B3A369', 'acc', 'ramblinwreck.com'),
    '97': ('Louisville', 'Cardinals', '#C9001F', 'acc', 'gocards.com'),
    '2390': ('Miami', 'Hurricanes', '#F47423', 'acc', 'miamihurricanes.com'),
    '152': ('NC State', 'Wolfpack', '#CC0000', 'acc', 'gopack.com'),
    '153': ('North Carolina', 'Tar Heels', '#7BAFD4', 'acc', 'goheels.com'),
    '87': ('Notre Dame', 'Fighting Irish', '#062340', 'acc', 'fightingirish.com'),
    '221': ('Pittsburgh', 'Panthers', '#003594', 'acc', 'pittsburghpanthers.com'),
    '2567': ('SMU', 'Mustangs', '#A80000', 'acc', 'smumustangs.com'),
    '24': ('Stanford', 'Cardinal', '#8C1515', 'acc', 'gostanford.com'),
    '183': ('Syracuse', 'Orange', '#000E54', 'acc', 'cuse.com'),
    '258': ('Virginia', 'Cavaliers', '#232D4B', 'acc', 'virginiasports.com'),
    '259': ('Virginia Tech', 'Hokies', '#6A2C3E', 'acc', 'hokiesports.com'),
    '154': ('Wake Forest', 'Demon Deacons', '#CEB888', 'acc', 'godeacs.com'),
    '356': ('Illinois', 'Fighting Illini', '#FF5F05', 'big10', 'fightingillini.com'),
    '84': ('Indiana', 'Hoosiers', '#970310', 'big10', 'iuhoosiers.com'),
    '2294': ('Iowa', 'Hawkeyes', '#231F20', 'big10', 'hawkeyesports.com'),
    '120': ('Maryland', 'Terrapins', '#CE1126', 'big10', 'umterps.com'),
    '130': ('Michigan', 'Wolverines', '#00274C', 'big10', 'mgoblue.com'),
    '127': ('Michigan State', 'Spartans', '#173F35', 'big10', 'msuspartans.com'),
    '135': ('Minnesota', 'Golden Gophers', '#5E0A2F', 'big10', 'gophersports.com'),
    '158': ('Nebraska', 'Cornhuskers', '#E31937', 'big10', 'huskers.com'),
    '77': ('Northwestern', 'Wildcats', '#492F92', 'big10', 'nusports.com'),
    '194': ('Ohio State', 'Buckeyes', '#BA0C2F', 'big10', 'ohiostatebuckeyes.com'),
    '2483': ('Oregon', 'Ducks', '#00934B', 'big10', 'goducks.com'),
    '213': ('Penn State', 'Nittany Lions', '#061440', 'big10', 'gopsusports.com'),
    '2509': ('Purdue', 'Boilermakers', '#CEB888', 'big10', 'purduesports.com'),
    '164': ('Rutgers', 'Scarlet Knights', '#CE0E2D', 'big10', 'scarletknights.com'),
    '26': ('UCLA', 'Bruins', '#2774AE', 'big10', 'uclabruins.com'),
    '30': ('USC', 'Trojans', '#9D2235', 'big10', 'usctrojans.com'),
    '264': ('Washington', 'Huskies', '#33006F', 'big10', 'gohuskies.com'),
    '275': ('Wisconsin', 'Badgers', '#A00000', 'big10', 'uwbadgers.com'),
    '12': ('Arizona', 'Wildcats', '#CC0033', 'big12', 'arizonawildcats.com'),
    '9': ('Arizona State', 'Sun Devils', '#FFC627', 'big12', 'thesundevils.com'),
    '252': ('BYU', 'Cougars', '#0047BA', 'big12', 'byucougars.com'),
    '239': ('Baylor', 'Bears', '#154734', 'big12', 'baylorbears.com'),
    '2132': ('Cincinnati', 'Bearcats', '#000000', 'big12', 'gobearcats.com'),
    '38': ('Colorado', 'Buffaloes', '#CFB87C', 'big12', 'cubuffs.com'),
    '248': ('Houston', 'Cougars', '#C8102E', 'big12', 'uhcougars.com'),
    '66': ('Iowa State', 'Cyclones', '#AE192D', 'big12', 'cyclones.com'),
    '2305': ('Kansas', 'Jayhawks', '#0051BA', 'big12', 'kuathletics.com'),
    '2306': ('Kansas State', 'Wildcats', '#330A57', 'big12', 'kstatesports.com'),
    '197': ('Oklahoma State', 'Cowboys', '#FE5C00', 'big12', 'okstate.com'),
    '2628': ('TCU', 'Horned Frogs', '#4D1979', 'big12', 'gofrogs.com'),
    '2641': ('Texas Tech', 'Red Raiders', '#DA291C', 'big12', 'texastech.com'),
    '2116': ('UCF', 'Knights', '#000000', 'big12', 'ucfknights.com'),
    '254': ('Utah', 'Utes', '#BE0000', 'big12', 'utahutes.com'),
    '277': ('West Virginia', 'Mountaineers', '#EAAA00', 'big12', 'wvusports.com'),
    '13': ('Cal Poly', 'Mustangs', '#1E4D2B', 'bigw', 'gopoly.com'),
    '2934': ('Cal State Bakersfield', 'Roadrunners', '#003BAB', 'bigw', 'gorunners.com'),
    '2239': ('Cal State Fullerton', 'Titans', '#003767', 'bigw', 'fullertontitans.com'),
    '2463': ('Cal State Northridge', 'Matadors', '#B50000', 'bigw', 'gomatadors.com'),
    '2856': ('California Baptist', 'Lancers', '#000080', 'bigw', 'cbulancers.com'),
    '299': ('Long Beach State', 'Beach', '#000000', 'bigw', 'longbeachstate.com'),
    '16': ('Sacramento State', 'Hornets', '#00573C', 'bigw', 'hornetsports.com'),
    '300': ('UC Irvine', 'Anteaters', '#002B5C', 'bigw', 'ucirvinesports.com'),
    '27': ('UC Riverside', 'Highlanders', '#14234F', 'bigw', 'gohighlanders.com'),
    '28': ('UC San Diego', 'Tritons', '#000000', 'bigw', 'ucsdtritons.com'),
    '2540': ('UC Santa Barbara', 'Gauchos', '#1E1840', 'bigw', 'ucsbgauchos.com'),
    '3084': ('Utah Valley', 'Wolverines', '#004812', 'bigw', 'gouvu.com'),
    # CAA
    '2097': ('Campbell', 'Fighting Camels', '#000000', 'caa', 'gocamels.com'),
    '232': ('Charleston', 'Cougars', '#7A2531', 'caa', 'cofcsports.com'),
    '2182': ('Drexel', 'Dragons', '#020260', 'caa', 'drexeldragons.com'),
    '2210': ('Elon', 'Phoenix', '#020303', 'caa', 'elonphoenix.com'),
    '2261': ('Hampton', 'Pirates', '#0067AC', 'caa', 'hamptonpirates.com'),
    '2275': ('Hofstra', 'Pride', '#003594', 'caa', 'gohofstra.com'),
    '2405': ('Monmouth', 'Hawks', '#051844', 'caa', 'monmouthhawks.com'),
    '2448': ('North Carolina A&T', 'Aggies', '#0505AA', 'caa', 'ncataggies.com'),
    '111': ('Northeastern', 'Huskies', '#CC0001', 'caa', 'gonu.com'),
    '2619': ('Stony Brook', 'Seawolves', '#990000', 'caa', 'stonybrookathletics.com'),
    '119': ('Towson', 'Tigers', '#FFC229', 'caa', 'towsontigers.com'),
    '350': ('UNC Wilmington', 'Seahawks', '#00665E', 'caa', 'uncwsports.com'),
    '2729': ('William & Mary', 'Tribe', '#115740', 'caa', 'tribeathletics.com'),
    # Pac-12
    '68': ('Boise State', 'Broncos', '#0033A0', 'pac12', 'broncosports.com'),
    '36': ('Colorado State', 'Rams', '#004C23', 'pac12', 'csurams.com'),
    '278': ('Fresno State', 'Bulldogs', '#B1102B', 'pac12', 'gobulldogs.com'),
    '2250': ('Gonzaga', 'Bulldogs', '#041E42', 'pac12', 'gozags.com'),
    '204': ('Oregon State', 'Beavers', '#DC4405', 'pac12', 'osubeavers.com'),
    '21': ('San Diego State', 'Aztecs', '#A6192E', 'pac12', 'goaztecs.com'),
    '326': ('Texas State', 'Bobcats', '#501214', 'pac12', 'txst.com'),
    '328': ('Utah State', 'Aggies', '#0F2439', 'pac12', 'utahstateaggies.com'),
    '265': ('Washington State', 'Cougars', '#A60F2D', 'pac12', 'wsucougars.com'),
    '333': ('Alabama', 'Crimson Tide', '#9E1B32', 'sec', 'rolltide.com'),
    '8': ('Arkansas', 'Razorbacks', '#A32136', 'sec', 'arkansasrazorbacks.com'),
    '2': ('Auburn', 'Tigers', '#002B5C', 'sec', 'auburntigers.com'),
    '57': ('Florida', 'Gators', '#0021A5', 'sec', 'floridagators.com'),
    '61': ('Georgia', 'Bulldogs', '#BA0C2F', 'sec', 'georgiadogs.com'),
    '96': ('Kentucky', 'Wildcats', '#0033A0', 'sec', 'ukathletics.com'),
    '99': ('LSU', 'Tigers', '#461D76', 'sec', 'lsusports.net'),
    '344': ('Mississippi State', 'Bulldogs', '#5D1725', 'sec', 'hailstate.com'),
    '142': ('Missouri', 'Tigers', '#F1B82D', 'sec', 'mutigers.com'),
    '201': ('Oklahoma', 'Sooners', '#990000', 'sec', 'soonersports.com'),
    '145': ('Ole Miss', 'Rebels', '#13294B', 'sec', 'olemisssports.com'),
    '2579': ('South Carolina', 'Gamecocks', '#73000A', 'sec', 'gamecocksonline.com'),
    '2633': ('Tennessee', 'Volunteers', '#FF8200', 'sec', 'utsports.com'),
    '251': ('Texas', 'Longhorns', '#AF5C37', 'sec', 'texassports.com'),
    '245': ('Texas A&M', 'Aggies', '#500000', 'sec', '12thman.com'),
    '238': ('Vanderbilt', 'Commodores', '#000000', 'sec', 'vucommodores.com'),
    # Big East
    '2086': ('Butler', 'Bulldogs', '#0D1361', 'bigeast', 'butlersports.com'),
    '156': ('Creighton', 'Bluejays', '#005CA9', 'bigeast', 'gocreighton.com'),
    '305': ('DePaul', 'Blue Demons', '#2D649C', 'bigeast', 'depaulbluedemons.com'),
    '46': ('Georgetown', 'Hoyas', '#110E42', 'bigeast', 'guhoyas.com'),
    '269': ('Marquette', 'Golden Eagles', '#003366', 'bigeast', 'gomarquette.com'),
    '2507': ('Providence', 'Friars', '#000000', 'bigeast', 'friars.com'),
    '2550': ('Seton Hall', 'Pirates', '#0857B1', 'bigeast', 'shupirates.com'),
    '2599': ("St. John's", 'Red Storm', '#D10000', 'bigeast', 'redstormsports.com'),
    '41': ('UConn', 'Huskies', '#0C2340', 'bigeast', 'uconnhuskies.com'),
    '222': ('Villanova', 'Wildcats', '#00205B', 'bigeast', 'villanova.com'),
    '2752': ('Xavier', 'Musketeers', '#21304E', 'bigeast', 'goxavier.com'),
    # Mountain West
    '2005': ('Air Force', 'Falcons', '#003594', 'mwc', 'goairforcefalcons.com'),
    '2253': ('Grand Canyon', 'Lopes', '#522398', 'mwc', 'gculopes.com'),
    '62': ("Hawai'i", 'Rainbow Warriors', '#005737', 'mwc', 'hawaiiathletics.com'),
    '2440': ('Nevada', 'Wolf Pack', '#041E42', 'mwc', 'nevadawolfpack.com'),
    '167': ('New Mexico', 'Lobos', '#BA0C2F', 'mwc', 'golobos.com'),
    '23': ('San José State', 'Spartans', '#0038A8', 'mwc', 'sjsuspartans.com'),
    '302': ('UC Davis', 'Aggies', '#002855', 'mwc', 'ucdavisaggies.com'),
    '2439': ('UNLV', 'Rebels', '#CF0A2C', 'mwc', 'unlvrebels.com'),
    '2638': ('UTEP', 'Miners', '#FF8200', 'mwc', 'utepminers.com'),
    '2751': ('Wyoming', 'Cowboys', '#492F24', 'mwc', 'gowyo.com'),
    # West Coast
    '2172': ('Denver', 'Pioneers', '#98002E', 'wcc', 'denverpioneers.com'),
    '2351': ('Loyola Marymount', 'Lions', '#880029', 'wcc', 'lmulions.com'),
    '279': ('Pacific', 'Tigers', '#F47820', 'wcc', 'pacifictigers.com'),
    '2492': ('Pepperdine', 'Waves', '#003A72', 'wcc', 'pepperdinewaves.com'),
    '2501': ('Portland', 'Pilots', '#330072', 'wcc', 'portlandpilots.com'),
    '2608': ("Saint Mary's", 'Gaels', '#D80024', 'wcc', 'smcgaels.com'),
    '301': ('San Diego', 'Toreros', '#2F99D4', 'wcc', 'usdtoreros.com'),
    '2539': ('San Francisco', 'Dons', '#005A36', 'wcc', 'usfdons.com'),
    '2541': ('Santa Clara', 'Broncos', '#690B0B', 'wcc', 'santaclarabroncos.com'),
    '2547': ('Seattle U', 'Redhawks', '#BF2E1A', 'wcc', 'goseattleu.com'),
    # American
    '2429': ('Charlotte', '49ers', '#005035', 'aac', 'charlotte49ers.com'),
    '151': ('East Carolina', 'Pirates', '#582C83', 'aac', 'ecupirates.com'),
    '2226': ('Florida Atlantic', 'Owls', '#003366', 'aac', 'fausports.com'),
    '235': ('Memphis', 'Tigers', '#004991', 'aac', 'gotigersgo.com'),
    '249': ('North Texas', 'Mean Green', '#068F33', 'aac', 'meangreensports.com'),
    '242': ('Rice', 'Owls', '#00205B', 'aac', 'riceowls.com'),
    '58': ('South Florida', 'Bulls', '#006747', 'aac', 'gousfbulls.com'),
    '218': ('Temple', 'Owls', '#A41E35', 'aac', 'owlsports.com'),
    '2655': ('Tulane', 'Green Wave', '#006747', 'aac', 'tulanegreenwave.com'),
    '202': ('Tulsa', 'Golden Hurricane', '#003595', 'aac', 'tulsahurricane.com'),
    '5': ('UAB', 'Blazers', '#1A5632', 'aac', 'uabsports.com'),
    '2636': ('UTSA', 'Roadrunners', '#0C2340', 'aac', 'goutsa.com'),
    '2724': ('Wichita State', 'Shockers', '#FFCD00', 'aac', 'goshockers.com'),
    # Southland
    '2837': ('East Texas A&M', 'Lions', '#000000', 'southland', 'lionathletics.com'),
    '2277': ('Houston Christian', 'Huskies', '#00539C', 'southland', 'hcuhuskies.com'),
    '2916': ('Incarnate Word', 'Cardinals', '#000000', 'southland', 'uiwcardinals.com'),
    '2320': ('Lamar', 'Cardinals', '#000000', 'southland', 'lamarcardinals.com'),
    '2377': ('McNeese', 'Cowboys', '#00529C', 'southland', 'mcneesesports.com'),
    '2443': ('New Orleans', 'Privateers', '#461D7C', 'southland', 'unoprivateers.com'),
    '2447': ('Nicholls', 'Colonels', '#C41230', 'southland', 'geauxcolonels.com'),
    '2466': ('Northwestern State', 'Demons', '#492F91', 'southland', 'nsudemons.com'),
    '2545': ('SE Louisiana', 'Lions', '#215732', 'southland', 'lionsports.net'),
    '2617': ('Stephen F. Austin', 'Lumberjacks', '#393996', 'southland', 'sfajacks.com'),
    '357': ('Texas A&M-Corpus Christi', 'Islanders', '#0067C5', 'southland', 'goislanders.com'),
    '292': ('UT Rio Grande Valley', 'Vaqueros', '#DC6000', 'southland', 'goutrgv.com'),
    # Atlantic 10
    '2166': ('Davidson', 'Wildcats', '#000000', 'a10', 'davidsonwildcats.com'),
    '2168': ('Dayton', 'Flyers', '#004B8D', 'a10', 'daytonflyers.com'),
    '2184': ('Duquesne', 'Dukes', '#002D62', 'a10', 'goduquesne.com'),
    '2230': ('Fordham', 'Rams', '#830032', 'a10', 'fordhamsports.com'),
    '2244': ('George Mason', 'Patriots', '#016600', 'a10', 'gomason.com'),
    '45': ('George Washington', 'Revolutionaries', '#002843', 'a10', 'gwsports.com'),
    '2325': ('La Salle', 'Explorers', '#003356', 'a10', 'goexplorers.com'),
    '2350': ('Loyola Chicago', 'Ramblers', '#9D1244', 'a10', 'loyolaramblers.com'),
    '227': ('Rhode Island', 'Rams', '#091F3F', 'a10', 'gorhody.com'),
    '257': ('Richmond', 'Spiders', '#9E0712', 'a10', 'richmondspiders.com'),
    '2603': ("Saint Joseph's", 'Hawks', '#9E1B32', 'a10', 'sjuhawks.com'),
    '139': ('Saint Louis', 'Billikens', '#00539C', 'a10', 'slubillikens.com'),
    '179': ('St. Bonaventure', 'Bonnies', '#70261D', 'a10', 'gobonnies.sbu.edu'),
    '2670': ('VCU', 'Rams', '#FFAF00', 'a10', 'vcuathletics.com'),
    # Missouri Valley
    '2057': ('Belmont', 'Bruins', '#182142', 'mvc', 'belmontbruins.com'),
    '71': ('Bradley', 'Braves', '#B70002', 'mvc', 'bradleybraves.com'),
    '2181': ('Drake', 'Bulldogs', '#005596', 'mvc', 'godrakebulldogs.com'),
    '339': ('Evansville', 'Purple Aces', '#663399', 'mvc', 'gopurpleaces.com'),
    '2287': ('Illinois State', 'Redbirds', '#CE1126', 'mvc', 'goredbirds.com'),
    '282': ('Indiana State', 'Sycamores', '#00669A', 'mvc', 'gosycamores.com'),
    '93': ('Murray State', 'Racers', '#002148', 'mvc', 'goracers.com'),
    '2460': ('Northern Iowa', 'Panthers', '#473282', 'mvc', 'unipanthers.com'),
    '79': ('Southern Illinois', 'Salukis', '#85283D', 'mvc', 'siusalukis.com'),
    '82': ('UIC', 'Flames', '#001E62', 'mvc', 'uicflames.com'),
    '2674': ('Valparaiso', 'Beacons', '#794500', 'mvc', 'valpoathletics.com'),
    # Sun Belt
    '2026': ('App State', 'Mountaineers', '#000000', 'sunbelt', 'appstatesports.com'),
    '2032': ('Arkansas State', 'Red Wolves', '#CC092F', 'sunbelt', 'astateredwolves.com'),
    '324': ('Coastal Carolina', 'Chanticleers', '#006F71', 'sunbelt', 'goccusports.com'),
    '290': ('Georgia Southern', 'Eagles', '#041E42', 'sunbelt', 'gseagles.com'),
    '2247': ('Georgia State', 'Panthers', '#0039A6', 'sunbelt', 'georgiastatesports.com'),
    '256': ('James Madison', 'Dukes', '#450084', 'sunbelt', 'jmusports.com'),
    '309': ('Louisiana', "Ragin' Cajuns", '#CE181E', 'sunbelt', 'ragincajuns.com'),
    '2348': ('Louisiana Tech', 'Bulldogs', '#003087', 'sunbelt', 'latechsports.com'),
    '276': ('Marshall', 'Thundering Herd', '#00B140', 'sunbelt', 'herdzone.com'),
    '295': ('Old Dominion', 'Monarchs', '#003768', 'sunbelt', 'odusports.com'),
    '6': ('South Alabama', 'Jaguars', '#00205B', 'sunbelt', 'usajaguars.com'),
    '2572': ('Southern Miss', 'Golden Eagles', '#FFC72C', 'sunbelt', 'southernmiss.com'),
    '2653': ('Troy', 'Trojans', '#862633', 'sunbelt', 'troytrojans.com'),
    '2433': ('UL Monroe', 'Warhawks', '#840029', 'sunbelt', 'ulmwarhawks.com'),
    # Conference USA
    '48': ('Delaware', 'Blue Hens', '#00539F', 'cusa', 'bluehens.com'),
    '2229': ('FIU', 'Panthers', '#091F3F', 'cusa', 'fiusports.com'),
    '55': ('Jacksonville State', 'Gamecocks', '#CC0000', 'cusa', 'jsugamecocksports.com'),
    '338': ('Kennesaw State', 'Owls', '#FDBB30', 'cusa', 'ksuowls.com'),
    '2335': ('Liberty', 'Flames', '#0A254E', 'cusa', 'libertyflames.com'),
    '2393': ('Middle Tennessee', 'Blue Raiders', '#036EB7', 'cusa', 'goblueraiders.com'),
    '2623': ('Missouri State', 'Bears', '#5E0009', 'cusa', 'missouristatebears.com'),
    '166': ('New Mexico State', 'Aggies', '#7E141B', 'cusa', 'nmstatesports.com'),
    '2534': ('Sam Houston', 'Bearkats', '#F56423', 'cusa', 'gobearkats.com'),
    '98': ('Western Kentucky', 'Hilltoppers', '#E13A3E', 'cusa', 'wkusports.com'),
    # Mid-American
    '2006': ('Akron', 'Zips', '#041E42', 'mac', 'gozips.com'),
    '2050': ('Ball State', 'Cardinals', '#BA0C2F', 'mac', 'ballstatesports.com'),
    '189': ('Bowling Green', 'Falcons', '#FD5000', 'mac', 'bgsufalcons.com'),
    '2084': ('Buffalo', 'Bulls', '#005BBB', 'mac', 'ubbulls.com'),
    '2117': ('Central Michigan', 'Chippewas', '#4C0027', 'mac', 'cmuchippewas.com'),
    '2199': ('Eastern Michigan', 'Eagles', '#006938', 'mac', 'emueagles.com'),
    '2309': ('Kent State', 'Golden Flashes', '#002664', 'mac', 'kentstatesports.com'),
    '113': ('UMass', 'Minutemen', '#881C1C', 'mac', 'umassathletics.com'),
    '193': ('Miami (OH)', 'RedHawks', '#C41230', 'mac', 'miamiredhawks.com'),
    '195': ('Ohio', 'Bobcats', '#154734', 'mac', 'ohiobobcats.com'),
    '2649': ('Toledo', 'Rockets', '#0B2240', 'mac', 'utrockets.com'),
    '2711': ('Western Michigan', 'Broncos', '#532E1F', 'mac', 'wmubroncos.com'),
    # Ivy
    '225': ('Brown', 'Bears', '#411E09', 'ivy', 'brownbears.com'),
    '171': ('Columbia', 'Lions', '#7BA4DB', 'ivy', 'gocolumbialions.com'),
    '172': ('Cornell', 'Big Red', '#B31B1B', 'ivy', 'cornellbigred.com'),
    '159': ('Dartmouth', 'Big Green', '#005730', 'ivy', 'dartmouthsports.com'),
    '108': ('Harvard', 'Crimson', '#990000', 'ivy', 'gocrimson.com'),
    '219': ('Penn', 'Quakers', '#082A74', 'ivy', 'pennathletics.com'),
    '163': ('Princeton', 'Tigers', '#000000', 'ivy', 'goprincetontigers.com'),
    '43': ('Yale', 'Bulldogs', '#004A81', 'ivy', 'yalebulldogs.com'),
    # Southern
    '236': ('Chattanooga', 'Mocs', '#00386B', 'socon', 'gomocs.com'),
    '2193': ('East Tennessee State', 'Buccaneers', '#002D61', 'socon', 'etsubucs.com'),
    '231': ('Furman', 'Paladins', '#582C83', 'socon', 'furmanpaladins.com'),
    '2382': ('Mercer', 'Bears', '#FF7F29', 'socon', 'mercerbears.com'),
    '2535': ('Samford', 'Bulldogs', '#005485', 'socon', 'samfordsports.com'),
    '2635': ('Tennessee Tech', 'Golden Eagles', '#5A4099', 'socon', 'ttusports.com'),
    '2643': ('The Citadel', 'Bulldogs', '#7BADD3', 'socon', 'citadelsports.com'),
    '2430': ('UNC Greensboro', 'Spartans', '#003559', 'socon', 'uncgspartans.com'),
    '2678': ('VMI', 'Keydets', '#AE122A', 'socon', 'vmikeydets.com'),
    '2717': ('Western Carolina', 'Catamounts', '#492F91', 'socon', 'catamountsports.com'),
    '2747': ('Wofford', 'Terriers', '#533B22', 'socon', 'woffordterriers.com'),
    # Summit
    '140': ('Kansas City', 'Roos', '#004B87', 'summit', 'kcroos.com'),
    '155': ('North Dakota', 'Fighting Hawks', '#00A26B', 'summit', 'fightinghawks.com'),
    '2449': ('North Dakota State', 'Bison', '#01402A', 'summit', 'gobison.com'),
    '2437': ('Omaha', 'Mavericks', '#E3193E', 'summit', 'omavs.com'),
    '198': ('Oral Roberts', 'Golden Eagles', '#002462', 'summit', 'oruathletics.com'),
    '233': ('South Dakota', 'Coyotes', '#CD1241', 'summit', 'goyotes.com'),
    '2571': ('South Dakota State', 'Jackrabbits', '#0033A0', 'summit', 'gojacks.com'),
    '2900': ('St. Thomas', 'Tommies', '#000000', 'summit', 'tommiesports.com'),
    # Big Sky
    '331': ('Eastern Washington', 'Eagles', '#A10022', 'bigsky', 'goeags.com'),
    '70': ('Idaho', 'Vandals', '#000000', 'bigsky', 'govandals.com'),
    '304': ('Idaho State', 'Bengals', '#EF8C00', 'bigsky', 'isubengals.com'),
    '149': ('Montana', 'Grizzlies', '#751D4A', 'bigsky', 'gogriz.com'),
    '147': ('Montana State', 'Bobcats', '#00205C', 'bigsky', 'msubobcats.com'),
    '2464': ('Northern Arizona', 'Lumberjacks', '#003976', 'bigsky', 'nauathletics.com'),
    '2458': ('Northern Colorado', 'Bears', '#13558D', 'bigsky', 'uncbears.com'),
    '2502': ('Portland State', 'Vikings', '#00311E', 'bigsky', 'goviks.com'),
    '253': ('Southern Utah', 'Thunderbirds', '#C72026', 'bigsky', 'suutbirds.com'),
    '3101': ('Utah Tech', 'Trailblazers', '#000000', 'bigsky', 'utahtechtrailblazers.com'),
    '2692': ('Weber State', 'Wildcats', '#18005A', 'bigsky', 'weberstatesports.com'),
    # Horizon
    '325': ('Cleveland State', 'Vikings', '#006633', 'horizon', 'csuvikings.com'),
    '2174': ('Detroit Mercy', 'Titans', '#165B9E', 'horizon', 'detroittitans.com'),
    '2739': ('Green Bay', 'Phoenix', '#006633', 'horizon', 'greenbayphoenix.com'),
    '85': ('IU Indianapolis', 'Jaguars', '#A81F30', 'horizon', 'iuindyjags.com'),
    '270': ('Milwaukee', 'Panthers', '#000000', 'horizon', 'mkepanthers.com'),
    '2459': ('Northern Illinois', 'Huskies', '#C8102E', 'horizon', 'niuhuskies.com'),
    '94': ('Northern Kentucky', 'Norse', '#FFC82E', 'horizon', 'nkunorse.com'),
    '2473': ('Oakland', 'Golden Grizzlies', '#04091C', 'horizon', 'goldengrizzlies.com'),
    '2870': ('Purdue Fort Wayne', 'Mastodons', '#CFB991', 'horizon', 'gomastodons.com'),
    '2523': ('Robert Morris', 'Colonials', '#00214D', 'horizon', 'rmucolonials.com'),
    '2750': ('Wright State', 'Raiders', '#CBA052', 'horizon', 'wsuraiders.com'),
    '2754': ('Youngstown State', 'Penguins', '#E51935', 'horizon', 'ysusports.com'),
    # Atlantic Sun
    '91': ('Bellarmine', 'Knights', '#000000', 'asun', 'athletics.bellarmine.edu'),
    '526': ('Florida Gulf Coast', 'Eagles', '#00885A', 'asun', 'fgcuathletics.com'),
    '294': ('Jacksonville', 'Dolphins', '#00523E', 'asun', 'judolphins.com'),
    '288': ('Lipscomb', 'Bisons', '#20366C', 'asun', 'lipscombsports.com'),
    '2454': ('North Florida', 'Ospreys', '#004B8D', 'asun', 'unfospreys.com'),
    '2511': ('Queens', 'Royals', '#192C66', 'asun', 'queensathletics.com'),
    '56': ('Stetson', 'Hatters', '#0A5640', 'asun', 'gohatters.com'),
    '2697': ('West Florida', 'Argonauts', '#087DC2', 'asun', 'goargos.com'),
    # United Athletic
    '2000': ('Abilene Christian', 'Wildcats', '#592D82', 'uac', 'acusports.com'),
    '2046': ('Austin Peay', 'Governors', '#8E0B0B', 'uac', 'letsgopeay.com'),
    '2110': ('Central Arkansas', 'Bears', '#4F2D7F', 'uac', 'ucasports.com'),
    '2198': ('Eastern Kentucky', 'Colonels', '#660819', 'uac', 'ekusports.com'),
    '2031': ('Little Rock', 'Trojans', '#AD0000', 'uac', 'lrtrojans.com'),
    '2453': ('North Alabama', 'Lions', '#663399', 'uac', 'roarlions.com'),
    '2627': ('Tarleton State', 'Texans', '#4F2D7F', 'uac', 'tarletonsports.com'),
    '250': ('UT Arlington', 'Mavericks', '#004B7C', 'uac', 'utamavs.com'),
    '2698': ('West Georgia', 'Wolves', '#0033A1', 'uac', 'uwgathletics.com'),
    # Metro (MAAC)
    '2099': ('Canisius', 'Golden Griffins', '#004A81', 'maac', 'gogriffs.com'),
    '2217': ('Fairfield', 'Stags', '#C8102E', 'maac', 'fairfieldstags.com'),
    '314': ('Iona', 'Gaels', '#6F2C3E', 'maac', 'ionagaels.com'),
    '2363': ('Manhattan', 'Jaspers', '#4F8537', 'maac', 'gojaspers.com'),
    '2368': ('Marist', 'Red Foxes', '#E53730', 'maac', 'goredfoxes.com'),
    '2771': ('Merrimack', 'Warriors', '#2F4F93', 'maac', 'merrimackathletics.com'),
    '116': ("Mount St. Mary's", 'Mountaineers', '#005596', 'maac', 'mountathletics.com'),
    '315': ('Niagara', 'Purple Eagles', '#69207E', 'maac', 'purpleeagles.com'),
    '2514': ('Quinnipiac', 'Bobcats', '#041B43', 'maac', 'gobobcats.com'),
    '2520': ('Rider', 'Broncs', '#A80532', 'maac', 'gobroncs.com'),
    '2529': ('Sacred Heart', 'Pioneers', '#A40012', 'maac', 'sacredheartpioneers.com'),
    '2612': ("Saint Peter's", 'Peacocks', '#004CC2', 'maac', 'saintpeterspeacocks.com'),
    '2561': ('Siena', 'Saints', '#037961', 'maac', 'sienasaints.com'),
    # Ohio Valley
    '2197': ('Eastern Illinois', 'Panthers', '#004B83', 'ovc', 'eiupanthers.com'),
    '2815': ('Lindenwood', 'Lions', '#000000', 'ovc', 'lindenwoodlions.com'),
    '2413': ('Morehead State', 'Eagles', '#094FA3', 'ovc', 'msueagles.com'),
    '2565': ('SIU Edwardsville', 'Cougars', '#EB1C23', 'ovc', 'siuecougars.com'),
    '2546': ('Southeast Missouri State', 'Redhawks', '#C8102E', 'ovc', 'semoredhawks.com'),
    '88': ('Southern Indiana', 'Screaming Eagles', '#002D72', 'ovc', 'gousieagles.com'),
    '2634': ('Tennessee State', 'Tigers', '#171796', 'ovc', 'tsutigers.com'),
    '2630': ('UT Martin', 'Skyhawks', '#FF6700', 'ovc', 'utmsports.com'),
    '2710': ('Western Illinois', 'Leathernecks', '#4E1E8A', 'ovc', 'goleathernecks.com'),
    # Big South
    '2127': ('Charleston Southern', 'Buccaneers', '#2E3192', 'bigsouth', 'csusports.com'),
    '2241': ('Gardner-Webb', "Runnin' Bulldogs", '#C12535', 'bigsouth', 'gwusports.com'),
    '2272': ('High Point', 'Panthers', '#330072', 'bigsouth', 'highpointpanthers.com'),
    '2344': ('Longwood', 'Lancers', '#003273', 'bigsouth', 'longwoodlancers.com'),
    '2506': ('Presbyterian', 'Blue Hose', '#194896', 'bigsouth', 'gobluehose.com'),
    '2515': ('Radford', 'Highlanders', '#BC1515', 'bigsouth', 'radfordathletics.com'),
    '2908': ('South Carolina Upstate', 'Spartans', '#008545', 'bigsouth', 'upstatespartans.com'),
    '2427': ('UNC Asheville', 'Bulldogs', '#003DA5', 'bigsouth', 'uncabulldogs.com'),
    '2737': ('Winthrop', 'Eagles', '#9E0B0E', 'bigsouth', 'winthropeagles.com'),
    # Patriot
    '44': ('American', 'Eagles', '#C41130', 'patriot', 'aueagles.com'),
    '349': ('Army', 'Black Knights', '#000000', 'patriot', 'goarmywestpoint.com'),
    '104': ('Boston University', 'Terriers', '#CC0000', 'patriot', 'goterriers.com'),
    '2083': ('Bucknell', 'Bison', '#000060', 'patriot', 'bucknellbison.com'),
    '2142': ('Colgate', 'Raiders', '#821019', 'patriot', 'colgateathletics.com'),
    '107': ('Holy Cross', 'Crusaders', '#582C83', 'patriot', 'goholycross.com'),
    '322': ('Lafayette', 'Leopards', '#790000', 'patriot', 'goleopards.com'),
    '2329': ('Lehigh', 'Mountain Hawks', '#6C2B2A', 'patriot', 'lehighsports.com'),
    '2352': ('Loyola Maryland', 'Greyhounds', '#76A7A0', 'patriot', 'loyolagreyhounds.com'),
    '2426': ('Navy', 'Midshipmen', '#00225B', 'patriot', 'navysports.com'),
    # America East
    '2066': ('Binghamton', 'Bearcats', '#00614A', 'ameast', 'bubearcats.com'),
    '2803': ('Bryant', 'Bulldogs', '#000000', 'ameast', 'bryantbulldogs.com'),
    '311': ('Maine', 'Black Bears', '#127DBE', 'ameast', 'goblackbears.com'),
    '2885': ('NJIT', 'Highlanders', '#EE3024', 'ameast', 'njithighlanders.com'),
    '160': ('New Hampshire', 'Wildcats', '#004990', 'ameast', 'unhwildcats.com'),
    '399': ('UAlbany', 'Great Danes', '#3D2777', 'ameast', 'ualbanysports.com'),
    '2378': ('UMBC', 'Retrievers', '#000000', 'ameast', 'umbcretrievers.com'),
    '2349': ('UMass Lowell', 'River Hawks', '#00529C', 'ameast', 'goriverhawks.com'),
    '261': ('Vermont', 'Catamounts', '#154734', 'ameast', 'uvmathletics.com'),
    # Northeast
    '2115': ('Central Connecticut', 'Blue Devils', '#1B49A2', 'nec', 'ccsubluedevils.com'),
    '2130': ('Chicago State', 'Cougars', '#006700', 'nec', 'gocsucougars.com'),
    '161': ('Fairleigh Dickinson', 'Knights', '#72293C', 'nec', 'fduknights.com'),
    '2330': ('Le Moyne', 'Dolphins', '#00573F', 'nec', 'lemoynedolphins.com'),
    '112358': ('LIU', 'Sharks', '#50C9F7', 'nec', 'liuathletics.com'),
    '2385': ('Mercyhurst', 'Lakers', '#00471B', 'nec', 'hurstathletics.com'),
    '2441': ('New Haven', 'Chargers', '#041E42', 'nec', 'newhavenchargers.com'),
    '284': ('Stonehill', 'Skyhawks', '#2F2A85', 'nec', 'stonehillskyhawks.com'),
    '2681': ('Wagner', 'Seahawks', '#00483A', 'nec', 'wagnerathletics.com'),
    # SWAC
    '2010': ('Alabama A&M', 'Bulldogs', '#790000', 'swac', 'aamusports.com'),
    '2011': ('Alabama State', 'Hornets', '#E9A900', 'swac', 'bamastatesports.com'),
    '2016': ('Alcorn State', 'Braves', '#4B0058', 'swac', 'alcornsports.com'),
    '2029': ('Arkansas-Pine Bluff', 'Golden Lions', '#E0AA0F', 'swac', 'uapblionsroar.com'),
    '2065': ('Bethune-Cookman', 'Wildcats', '#7B1831', 'swac', 'bcuathletics.com'),
    '50': ('Florida A&M', 'Rattlers', '#F89728', 'swac', 'famuathletics.com'),
    '2755': ('Grambling', 'Tigers', '#EE8601', 'swac', 'gsutigers.com'),
    '2296': ('Jackson State', 'Tigers', '#123297', 'swac', 'gojsutigers.com'),
    '2400': ('Mississippi Valley State', 'Delta Devils', '#005328', 'swac', 'mvsusports.com'),
    '2504': ('Prairie View A&M', 'Panthers', '#582C83', 'swac', 'pvpanthers.com'),
    '2582': ('Southern', 'Jaguars', '#004B97', 'swac', 'gojagsports.com'),
    '2640': ('Texas Southern', 'Tigers', '#860038', 'swac', 'tsusports.com'),
    # MEAC
    '2154': ('Coppin State', 'Eagles', '#2E3192', 'meac', 'coppinstatesports.com'),
    '2169': ('Delaware State', 'Hornets', '#009CDB', 'meac', 'dsuhornets.com'),
    '47': ('Howard', 'Bison', '#003A63', 'meac', 'hubison.com'),
    '2379': ('Maryland Eastern Shore', 'Hawks', '#5C2301', 'meac', 'easternshorehawks.com'),
    '2415': ('Morgan State', 'Bears', '#014786', 'meac', 'morganstatebears.com'),
    '2450': ('Norfolk State', 'Spartans', '#0C8968', 'meac', 'nsuspartans.com'),
    '2428': ('North Carolina Central', 'Eagles', '#880023', 'meac', 'nccueaglepride.com'),
    '2569': ('South Carolina State', 'Bulldogs', '#7D1315', 'meac', 'scsuathletics.com'),
}

CONFERENCES = {
    'acc': ('ACC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/acc.png'),
    'big10': ('Big Ten', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_ten.png'),
    'big12': ('Big 12', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_12.png'),
    'sec': ('SEC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/sec.png'),
    'bigw': ('Big West', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_west.png'),
    'caa': ('CAA', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/colonial.png'),
    'pac12': ('Pac-12', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/pac_12.png'),
    'bigeast': ('Big East', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_east.png'),
    'mwc': ('Mountain West', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/mountain_west.png'),
    'wcc': ('WCC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/west_coast.png'),
    'aac': ('American', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/american.png'),
    'southland': ('Southland', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/southland.png'),
    'a10': ('Atlantic 10', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/atlantic_10.png'),
    'mvc': ('Missouri Valley', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/missouri_valley.png'),
    'sunbelt': ('Sun Belt', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/sun_belt.png'),
    'cusa': ('Conference USA', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/conference_usa.png'),
    'mac': ('MAC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/mid_american.png'),
    'ivy': ('Ivy League', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/ivy.png'),
    'socon': ('SoCon', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/southern.png'),
    'summit': ('Summit League', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/summit_league.png'),
    'bigsky': ('Big Sky', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_sky.png'),
    'horizon': ('Horizon League', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/horizon.png'),
    'asun': ('ASUN', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/atlantic_sun.png'),
    'uac': ('UAC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/uac.png'),
    'maac': ('Metro (MAAC)', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/metro.png'),
    'ovc': ('Ohio Valley', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/ohio_valley.png'),
    'bigsouth': ('Big South', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/big_south.png'),
    'patriot': ('Patriot League', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/patriot_league.png'),
    'ameast': ('America East', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/america_east.png'),
    'nec': ('NEC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/northeast.png'),
    'swac': ('SWAC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/swac.png'),
    'meac': ('MEAC', 'https://a.espncdn.com/i/teamlogos/ncaa_conf/500/meac.png'),
}
NCAA_LOGO = '/static/img/ncaa.svg'   # referee spot for games between conferences

GENDERS = {'mens': "Men's", 'womens': "Women's"}
# What a sheet can be; 'wvb' (women's volleyball) rosters come from the school sites, not ESPN
SHEETS = {'mens': "Men's basketball", 'womens': "Women's basketball", 'wvb': "Women's volleyball"}
# schools without a women's volleyball team: Boston University, Detroit Mercy, Drexel, George Mason, Longwood, Maine, Monmouth, Mount St. Mary's, Oklahoma State, Richmond, Saint Joseph's, St. Bonaventure, VMI, Vermont
NO_VOLLEYBALL = {'197', '2182', '2405', '2244', '257', '2603', '179', '2678', '2174', '116', '2344', '104', '311', '261'}
MENS_ONLY = {'2643', '2678'}   # The Citadel and VMI have no women's team

# Schools whose volleyball page isn't at /sports/womens-volleyball/roster or /sports/volleyball/roster
VB_PAGES = {
    'ramblinwreck.com': '/sports/w-volley/roster/',
    'hawkeyesports.com': '/sports/wvball/roster/',
    'golobos.com': '/sports/wvball/roster/',
    'roarlions.com': '/sports/wvball/roster/',
    'fightingirish.com': '/sports/wvball/roster/',
    'ohiobobcats.com': '/sports/wvball/roster/',
    'tommiesports.com': '/sports/wvball/roster/',
    'virginiasports.com': '/sports/wvball/roster/',
    'vucommodores.com': '/sports/wvolley/roster',
    'lsusports.net': '/sports/vb/roster',
    'ukathletics.com': '/sports/wvball/roster/',
    'gamecocksonline.com': '/sports/wvball/roster/',
}

# Schools whose basketball pages aren't at /sports/<mens|womens>-basketball/: (men's page, women's page)
STAFF_PAGES = {
    'arkansasrazorbacks.com': ('/sport/m-baskbl/roster/', '/sport/w-baskbl/roster/'),
    'ramblinwreck.com': ('/sports/m-baskbl/roster/', '/sports/w-baskbl/roster/'),
    'hawkeyesports.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'ukathletics.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'fightingirish.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'gamecocksonline.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'vucommodores.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'clemsontigers.com': (None, '/sports/w-basketball/roster'),
    'virginiasports.com': (None, '/sports/wbball/roster'),
    'golobos.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'ohiobobcats.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'tommiesports.com': ('/sports/mbball/roster/', '/sports/wbball/roster/'),
    'ttusports.com': ('/sports/mbkb/coaches/index', '/sports/wbkb/coaches/index'),
    'ccsubluedevils.com': ('/sports/mbkb/coaches/index', '/sports/wbkb/coaches/index'),
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/130.0 Safari/537.36'}
ROSTER_SECONDS = 10 * 60
STAFF_SECONDS = 6 * 3600

_cache = {}


def _cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def team_key(gender, team_id):
    """One id per team per gender, so each sheet remembers its own order and coaches"""
    return f"{'v' if gender == 'wvb' else gender[0]}{team_id}"


def logo_url(team_id):
    return f'https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png'


def teams():
    """[{id, school, name, color, conf, conf_name, logo}] grouped by conference, then school."""
    order = list(CONFERENCES)
    return sorted(({'id': i, 'school': s, 'name': n, 'color': c, 'conf': conf, 'conf_name': CONFERENCES[conf][0], 'logo': logo_url(i),
                    'mens_only': i in MENS_ONLY, 'no_vb': i in NO_VOLLEYBALL}
                   for i, (s, n, c, conf, _) in NCAA_TEAMS.items()), key=lambda t: (order.index(t['conf']), t['school']))


# ---------- Coaching staffs from school athletics sites ----------
def _nuxt(s):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m: return None
    try: return json.loads(m.group(1))
    except Exception: return None

def _from_sidearm_data(arr, base):
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and {'firstName', 'lastName', 'title', 'image'} <= o.keys():
            first, last, title, img = v(o['firstName']), v(o['lastName']), v(o['title']), v(o['image'])
            if not isinstance(title, str) or not isinstance(first, str): continue
            url = ''
            if isinstance(img, dict):
                url = v(img.get('absoluteUrl')) or v(img.get('url')) or ''
                if url: url = urljoin(base, url)
            out.append((f'{first} {last or ""}'.strip(), title, url))
    return out

def _from_wmt_data(arr, base):
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and {'first_name', 'last_name', 'position'} <= o.keys() and ('master_photo' in o or 'photo' in o):
            first, last, pos = v(o['first_name']), v(o['last_name']), v(o['position'])
            if not isinstance(pos, str) or not isinstance(first, str): continue
            photo = v(o.get('master_photo')) or v(o.get('photo'))
            url = v(photo.get('url')) if isinstance(photo, dict) else ''
            out.append((f'{first} {last or ""}'.strip(), pos, url or ''))
    return out

def _from_sidearm_cards(s, base):
    out = []
    for chunk in s.split('s-person-card ')[1:]:
        n = re.search(r'<h3[^>]*>(.*?)</h3>', chunk, re.S); p = re.search(r's-person-details__position[^>]*>\s*<div>(.*?)</div>', chunk, re.S)
        if not n or not p: continue
        img = re.search(r'crop\?url=([^&"]+)', chunk)
        out.append((html.unescape(re.sub('<.*?>', '', n.group(1))).strip(), html.unescape(p.group(1)).strip(), unquote(img.group(1)) if img else ''))
    return out

def _from_sidearm_classic(s, base):
    out = []
    for chunk in s.split('sidearm-roster-coach ')[1:]:
        n = re.search(r'sidearm-roster-coach-name[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)', chunk)
        p = re.search(r'sidearm-roster-coach-title[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)', chunk)
        img = re.search(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', chunk, re.I)
        if n and p:
            out.append((html.unescape(n.group(1)).strip(), html.unescape(p.group(1)).strip(), urljoin(base, img.group(1).split('?')[0]) if img else ''))
    return out

def _from_coach_links(s, base):
    """Older WMT/WordPress sites (Kentucky, South Carolina): each coach's photo and name link to /roster/coach/<name>/"""
    photos, out = {}, []
    for chunk in re.split(r'(?=<a\s+href="[^"]*/roster/coach/)', s)[1:]:
        anchor = chunk[:chunk.find('</a>') + 4] if '</a>' in chunk else chunk[:2000]
        name = re.search(r'(?:alt|title)="([^"]+)"', anchor) or re.search(r'>\s*([^<>]{3,60}?)\s*</a>$', anchor)
        if not name: continue
        name = html.unescape(name.group(1)).strip()
        img = re.search(r'(?:data-src|data-bg|src)="(https?://[^"]+/imgproxy/[^"]+)"', anchor)
        if img: photos.setdefault(name, img.group(1))
        pos = re.search(r'class="(?:roster-item__info|position)"[^>]*>\s*([^<]+)', chunk[:3000])
        if pos: out.append([name, html.unescape(pos.group(1)).strip(), ''])
    return [(n, p, photos.get(n, '')) for n, p, _ in out]

def _from_presto_cards(s, base):
    """PrestoSports sites (Tennessee Tech): coach cards with the photo as a background image"""
    out = []
    for chunk in s.split('coach-thumb ')[1:]:
        n = re.search(r'card-title[^>]*>\s*([^<]+?)\s*</a>', chunk); p = re.search(r'card-text[^>]*>\s*([^<]+?)\s*</p>', chunk)
        img = re.search(r"background-image:url\('([^'?]+)", chunk)
        if n and p:
            out.append((html.unescape(n.group(1)), html.unescape(p.group(1)), urljoin(base, img.group(1)) if img else ''))
    return out

def _from_staff_table(s, base):
    """Arkansas, and older SIDEARM coach tables (Utah State): names and titles; each coach's photo is on their own page"""
    rows = re.findall(r'<a href="([^"]*/coache/[^"]*)">\s*([^<]+?)\s*</a>\s*</td>\s*<td>\s*([^<]+?)\s*</td>', s) + [
        # a title can wrap onto two lines: "Assistant Coach<br>Recruiting Coordinator" (Austin Peay)
        (urljoin(base, page), name, re.sub(r'\s*<br\s*/?>\s*', ' / ', title)) for page, name, title in re.findall(
            r'sidearm-coaches-coach">\s*<th[^>]*>\s*<a[^>]*href=\'([^\']+)\'>\s*([^<]+?)\s*</a>\s*</th>\s*<td[^>]*>\s*((?:[^<]|<br\s*/?>)+?)\s*</td>', s)]
    def photo(page, name):
        try:
            p = requests.get(page, headers=HEADERS, timeout=10).text
            last = re.escape(name.split()[-1])
            # the coach's name in the photo's alt text, or in its file name (UTRGV labels every photo "WBB Headshots")
            m = (re.search(r'<img[^>]+src="([^"]+)"[^>]*alt="[^"]*' + last, p, re.I)
                 or re.search(r'<img[^>]+src="([^"]*/images/\d{4}/[^"]*' + last + r'[^"]*)"', p, re.I))
            return m.group(1) if m else ''
        except Exception:
            return ''
    coaches = [(page, html.unescape(name), html.unescape(title)) for page, name, title in rows if 'coach' in title.lower()]
    with ThreadPoolExecutor(5) as pool:
        photos = list(pool.map(lambda r: photo(r[0], r[1]), coaches))
    return [(name, title, url) for (_, name, title), url in zip(coaches, photos)]


def _sport_paths(domain, gender):
    if gender == 'wvb':
        return [VB_PAGES[domain]] if domain in VB_PAGES else ['/sports/womens-volleyball/roster', '/sports/volleyball/roster']
    custom = STAFF_PAGES.get(domain, (None, None))[0 if gender == 'mens' else 1]
    return [custom] if custom else [f'/sports/{gender}-basketball/coaches', f'/sports/{gender}-basketball/roster']


# A head coach titled only "Director of Women's Volleyball" (Stanford's "The Montag Family Director of Women's Volleyball")
_VB_HEAD = re.compile(r"director of (?:women['’]?s )?volleyball\s*$", re.I)


def _staff_from_site(domain, gender):
    paths = _sport_paths(domain, gender)
    if gender == 'wvb':   # some schools list volleyball coaches on their own page, not under the roster
        paths = paths + [re.sub(r'/roster/?$', '/coaches', p) for p in paths]
    not_bench = ('strength', 's&c', 'conditioning', 'performance', 'trainer', 'video', 'academic')
    keep = lambda cs: [c for c in cs if ('coach' in c[1].lower() or _VB_HEAD.search(c[1])) and not any(w in c[1].lower() for w in not_bench)]
    for path in paths:
        base = f'https://{domain}{path}'
        try:
            res = requests.get(base, headers=HEADERS, timeout=12)
        except Exception:
            continue
        if res.status_code != 200:
            continue
        arr = _nuxt(res.text)
        parsers = ([lambda: _from_sidearm_data(arr, base), lambda: _from_wmt_data(arr, base)] if arr else []) + [
            lambda: _from_sidearm_cards(res.text, base), lambda: _from_sidearm_classic(res.text, base),
            lambda: _from_coach_links(res.text, base), lambda: _from_presto_cards(res.text, base),
            lambda: _from_staff_table(res.text, base)]
        for parse in parsers:
            found = keep(parse())
            if len(found) >= (1 if gender == 'wvb' else 2):   # small volleyball staffs can be just the head coach
                seen, out = set(), []
                for name, title, photo in found:
                    # drop class years: "Jon Jaques '10" (Cornell), "David Richman ('02, '05)" (NDSU)
                    name = re.sub(r"\s*\(\s*['’]\d{2}(?:\s*,\s*['’]\d{2})*\s*\)|\s+['’]\d{2}\b", '', re.sub(r'\s+', ' ', name)).strip()
                    if re.sub(r'[^a-z]', '', name.lower()) in ('tba', 'tbd'):   # open spot (Charleston Southern)
                        continue
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        out.append({'name': name, 'role': re.sub(r'\s+', ' ', title).strip(), 'headshot': photo or None})
                return out
    return []


def _rank(role):
    r = role.lower()
    if 'associate head' in r or 'assistant head' in r:
        return 1
    if 'advisor' in r or 'to the head' in r:
        return 3
    if _VB_HEAD.search(r):
        return 0
    # endowed Ivy titles: "Elizabeth F. Turner '98 Coaching Chair for Women's Basketball" (Brown)
    if ('head' in r or 'coaching chair' in r) and 'assistant' not in r and 'associate' not in r:
        return 0
    if 'associate' in r:
        return 1
    if r.startswith('assistant coach') or 'assistant coach' in r:
        return 2
    return 3


def _coaches(team_id, gender, espn_head):
    domain = NCAA_TEAMS[team_id][4]
    def load():
        try:
            staff = _staff_from_site(domain, gender)
        except Exception as e:
            logger.warning(f"NCAA STAFF FAIL | {domain} {gender} | {e}")
            staff = []
        if not staff:
            logger.info(f"NCAA STAFF | {domain} {gender} | none found, using ESPN's head coach")
        return staff
    staff = list(_cached(f'staff:{domain}:{gender}', STAFF_SECONDS, load))
    if espn_head and not any(_rank(c['role']) == 0 for c in staff):
        staff.insert(0, {'name': espn_head, 'role': 'Head Coach', 'headshot': None})
    staff.sort(key=lambda c: _rank(c['role']))   # stable: keeps the school's order within each group
    for c in staff:
        c['id'] = re.sub(r'[^a-z0-9]+', '-', c['name'].lower()).strip('-')
    return staff


# ---------- Rosters from ESPN ----------
def _roster(gender, team_id):
    def load():
        res = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{gender}-college-basketball/teams/{team_id}/roster',
                           headers=HEADERS, timeout=15)
        if res.status_code == 404:
            return _roster_by_player(gender, team_id) or res.raise_for_status()
        res.raise_for_status()
        return res.json()
    return _cached(f'roster:{gender}:{team_id}', ROSTER_SECONDS, load)


def _roster_by_player(gender, team_id):
    """ESPN's roster call 404s when one player's record is missing (Mercer men): load the players one by one, skipping that one"""
    league = f'https://sports.core.api.espn.com/v2/sports/basketball/leagues/{gender}-college-basketball'
    try:
        season = requests.get(league + '/seasons', headers=HEADERS, timeout=10).json()['items'][0]['$ref'].split('/seasons/')[1].split('?')[0]
        refs = [r['$ref'] for r in requests.get(f'{league}/seasons/{season}/teams/{team_id}/athletes?limit=60', headers=HEADERS, timeout=10).json()['items']]
    except Exception:
        return None
    def one(ref):
        try:
            res = requests.get(ref.replace('http://', 'https://'), headers=HEADERS, timeout=10)
            return res.json() if res.ok else None
        except Exception:
            return None
    with ThreadPoolExecutor(8) as pool:
        athletes = [a for a in pool.map(one, refs) if a]
    return {'athletes': athletes} if athletes else None


# ---------- Volleyball rosters from the school sites (ESPN has none) ----------
def _vb_players_sidearm(arr, base):
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and 'jerseyNumber' in o and 'firstName' in o and 'lastName' in o:
            first, last = v(o['firstName']), v(o['lastName'])
            if not isinstance(first, str):
                continue
            img = v(o.get('image')); url = ''
            if isinstance(img, dict):
                url = v(img.get('absoluteUrl')) or v(img.get('url')) or ''
            out.append((first, last or '', v(o['jerseyNumber']), v(o.get('positionShort')), urljoin(base, url) if url else ''))
    return out

def _vb_players_wmt(arr, base):
    """WMT sites (Stanford): each roster entry points at a player, a position and a photo"""
    v = lambda i: arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None
    out = []
    for o in arr:
        if isinstance(o, dict) and 'jersey_number' in o and 'player' in o:
            pl, pos, ph = v(o['player']), v(o.get('player_position')), v(o.get('photo'))
            if not isinstance(pl, dict) or not isinstance(v(pl.get('first_name')), str):
                continue
            out.append((v(pl.get('first_name')), v(pl.get('last_name')) or '', v(o.get('jersey_number_label')) or v(o['jersey_number']),
                        v(pos.get('abbreviation')) if isinstance(pos, dict) else '', (v(ph.get('url')) if isinstance(ph, dict) else '') or ''))
    return out

def _vb_players_classic(s, base):
    """Older SIDEARM roster pages: one <li class="sidearm-roster-player"> per player"""
    out = []
    for chunk in re.split(r'class="sidearm-roster-player[ "]', s)[1:]:
        num = re.search(r'sidearm-roster-player-jersey-number[^>]*>\s*(\d+)', chunk)
        name = re.search(r'sidearm-roster-player-name[^>]*>.*?<a[^>]*>\s*([^<]+?)\s*</a>', chunk, re.S)
        pos = re.search(r'sidearm-roster-player-position[^>]*>\s*(?:<[^>]+>\s*)*([^<]+?)\s*<', chunk)
        img = re.search(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', chunk, re.I)
        if num and name:
            first, _, last = html.unescape(name.group(1)).strip().partition(' ')
            out.append((first, last, num.group(1), html.unescape(pos.group(1)).strip() if pos else '', urljoin(base, img.group(1).split('?')[0]) if img else ''))
    return out

def _vb_players_links(s, base):
    """Older WMT pages (Kentucky, South Carolina, LSU): each player's photo, number, name and position sit around a /roster/player/ link"""
    cards = []   # a player's photo link and name link are separate <a>s: keep them together
    for chunk in re.split(r'(?=<a[^>]+href="[^"]*/roster/player/)', s)[1:]:
        slug = re.search(r'/roster/player/([^/"]+)', chunk).group(1)
        if cards and cards[-1][0] == slug:
            cards[-1][1] += chunk
        else:
            cards.append([slug, chunk])
    out, seen = [], set()
    for slug, chunk in cards:
        if slug in seen:
            continue
        part = chunk[:5000]
        name = (re.search(r'(?:title|alt)="([^"]{3,60})"', part) or re.search(r'roster-item__name[^>]*>\s*([^<]+)', part)
                or re.search(r'person__name[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*<', part))
        num = re.search(r'class="[^"]*number[^"]*"[^>]*>\s*#?(\d{1,2})\s*<', part)
        pos = (re.search(r'class="[^"]*position[^"]*"[^>]*>\s*([A-Z/]{1,7})\s*<', part)
               or re.search(r'roster-item__info[^>]*>\s*([A-Z/]{1,7})\s*-', part))
        img = re.search(r'(?:data-src|data-bg|src)="(https?://[^"]+/imgproxy/[^"]+)"', part)
        if name and num:
            seen.add(slug)
            first, _, last = html.unescape(name.group(1)).strip().partition(' ')
            out.append((first, last, num.group(1), pos.group(1) if pos else '', img.group(1) if img else ''))
    return out

def _vb_players_json(s, base):
    """SIDEARM table rosters (Bradley, Utah State) carry the players as JSON in the page"""
    i = s.find('"players":[{')
    if i < 0:
        return []
    try:
        arr, _ = json.JSONDecoder().raw_decode(s[i + 10:])
    except Exception:
        return []
    return [(p.get('first_name') or '', p.get('last_name') or '', p.get('jersey_number'), p.get('position_short') or '',
             urljoin(base, (p.get('image') or {}).get('fullpath') or '') if (p.get('image') or {}).get('fullpath') else '')
            for p in arr if isinstance(p, dict) and p.get('first_name')]

_POS = {'outside hitter': 'OH', 'outside': 'OH', 'middle blocker': 'MB', 'middle': 'MB', 'setter': 'S', 'opposite': 'OPP',
        'opposite hitter': 'OPP', 'right side': 'RS', 'right side hitter': 'RS', 'libero': 'L', 'defensive specialist': 'DS', 'pin hitter': 'PIN'}

def _short_pos(pos):
    """'Defensive Specialist / Libero' -> 'DS/L' (LSU spells them out)"""
    parts = [p.strip() for p in re.split(r'[/,]', pos or '') if p.strip()]
    return '/'.join(_POS.get(p.lower(), p) for p in parts)

def _vb_players(domain):
    for path in _sport_paths(domain, 'wvb'):
        base = f'https://{domain}{path}'
        try:
            res = requests.get(base, headers=HEADERS, timeout=12)
        except Exception:
            continue
        if res.status_code != 200:
            continue
        arr = _nuxt(res.text)
        for found in ([_vb_players_sidearm(arr, base), _vb_players_wmt(arr, base)] if arr else []) + [
                _vb_players_classic(res.text, base), _vb_players_links(res.text, base), _vb_players_json(res.text, base)]:
            if len(found) >= 6:
                seen, out = set(), []
                for first, last, num, pos, photo in found:
                    if (first, last) in seen:
                        continue
                    seen.add((first, last))
                    if photo and '/images/' in photo and '?' not in photo:
                        photo += '?width=400'   # SIDEARM serves full-size originals otherwise
                    out.append({'id': re.sub(r'[^a-z0-9]+', '-', f'{first} {last}'.lower()).strip('-'), 'first': first.strip(), 'last': last.strip(),
                                'number': str(num or '').strip(), 'pos': _short_pos(pos), 'headshot': photo or None})
                return out
    return []


def team_data(gender, team_id):
    """Everything the sheet needs for one team: players (jersey order) and coaching staff."""
    school, name, color, conf, domain = NCAA_TEAMS[team_id]
    players, espn_head = [], ''
    if gender == 'wvb':
        players = list(_cached(f'vbroster:{domain}', ROSTER_SECONDS, lambda: _vb_players(domain)))
    else:
        data = _roster(gender, team_id)
        for a in data.get('athletes') or []:
            players.append({'id': str(a['id']), 'first': a.get('firstName') or '', 'last': a.get('lastName') or a.get('displayName') or '',
                            'number': str(a.get('jersey') or '').strip(), 'pos': (a.get('position') or {}).get('abbreviation') or '',
                            'headshot': (a.get('headshot') or {}).get('href')})
        head = data.get('coach') or []
        espn_head = f"{head[0].get('firstName', '')} {head[0].get('lastName', '')}".strip() if head else ''
    players.sort(key=lambda p: (not p['number'].isdigit(), int(p['number']) if p['number'].isdigit() else 0, p['last']))
    return {'abbr': team_key(gender, team_id), 'city': school, 'name': name, 'color': color, 'conf': conf,
            'logo': logo_url(team_id), 'players': players, 'coaches': _coaches(team_id, gender, espn_head)}


def matchup(gender, away, home):
    with ThreadPoolExecutor(2) as pool:
        teams = list(pool.map(lambda t: team_data(gender, t), [away, home]))
    conf = teams[0]['conf'] if teams[0]['conf'] == teams[1]['conf'] else None
    return {'teams': teams, 'gender': gender,
            'ref_logo': CONFERENCES[conf][1] if conf else NCAA_LOGO,
            'ref_logo_alt': CONFERENCES[conf][0] if conf else 'NCAA'}
