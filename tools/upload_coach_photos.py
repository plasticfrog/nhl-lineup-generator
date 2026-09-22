"""Upload a folder of coach photos so they appear on everyone's lineup sheets.

Name each file after the coach, e.g. "Ryan Warsofsky.jpg" or "ryan_warsofsky.png".

    python3 tools/upload_coach_photos.py ~/Desktop/coach-photos --site https://your-app.up.railway.app
"""
import argparse
import os
import sys

import requests

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def coach_name_from_file(filename):
    stem = os.path.splitext(filename)[0]
    return ' '.join(stem.replace('_', ' ').replace('-', ' ').split()).upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder')
    parser.add_argument('--site', default='http://localhost:5055', help='lineup generator base URL')
    args = parser.parse_args()

    files = sorted(f for f in os.listdir(args.folder) if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
    if not files:
        sys.exit(f'No images found in {args.folder}')

    failed = 0
    for f in files:
        name = coach_name_from_file(f)
        with open(os.path.join(args.folder, f), 'rb') as fh:
            res = requests.post(f"{args.site.rstrip('/')}/coach_photos",
                                data={'name': name}, files={'photo': (f, fh)}, timeout=60)
        if res.ok:
            print(f'  saved   {name}')
        else:
            failed += 1
            print(f'  FAILED  {name}: {res.text.strip()[:120]}')
    print(f'{len(files) - failed} of {len(files)} photos uploaded to {args.site}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
