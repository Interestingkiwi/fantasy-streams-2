"""
Scrapes rookies from EliteProspects (Temp until work out API License)
Author - Jason Druckenmiller
Created - 7/1/2026
Updated - 7/2/2026
"""


import cloudscraper
from bs4 import BeautifulSoup
import pandas as pd
from db_config import engine
import time
import re

def scrape_ep_league_leaders(league="whl", season="2025-2026", limit=50):
    """
    Scrapes EP using a dynamic column-mapping strategy immune to CSS changes.
    Now hunts for PIM, Hits, and Blocks if available.
    """
    url = f"https://www.eliteprospects.com/league/{league}/stats/{season}"
    print(f"Scraping {url}...")

    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    response = scraper.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # 1. Bulletproof Table Finder
    stats_table = None
    for tbl in soup.find_all('table'):
        headers = [th.text.strip().lower() for th in tbl.find_all('th')]
        if 'player' in headers and ('tp' in headers or 'pts' in headers):
            stats_table = tbl
            break

    if not stats_table:
        print(" -> [FAILED] Could not find the stats table.")
        return None

    # 2. Upgraded Dynamic Column Mapping
    headers = stats_table.find_all('th')
    col_idx = {}
    for idx, th in enumerate(headers):
        text = th.text.strip().lower()
        if 'player' in text: col_idx['player'] = idx
        elif text == 'gp': col_idx['gp'] = idx
        elif text == 'g': col_idx['g'] = idx
        elif text == 'a': col_idx['a'] = idx
        elif text in ['tp', 'pts']: col_idx['tp'] = idx
        elif text == 'pim': col_idx['pim'] = idx
        elif text in ['hit', 'hits']: col_idx['hits'] = idx
        elif text in ['blk', 'blocks']: col_idx['blocks'] = idx

    # 3. Parse the rows
    players_data = []

    if stats_table.find('tbody'):
        rows = stats_table.find('tbody').find_all('tr')
    else:
        rows = stats_table.find_all('tr')[1:]

    count = 0
    for row in rows:
        if count >= limit:
            break

        cells = row.find_all(['td', 'th'])

        # Skip weird spacer rows
        if len(cells) < max(col_idx.values()) + 1:
            continue

        player_cell = cells[col_idx.get('player', 1)]
        link_tag = player_cell.find('a', href=re.compile(r'/player/'))

        if not link_tag:
            continue

        player_name = link_tag.text.strip()
        profile_url = link_tag['href']

        match = re.search(r'/player/(\d+)/', profile_url)
        ep_id = match.group(1) if match else None

        if not ep_id:
            continue

        # Safely grab stats, default to 0 if the column doesn't exist on EP
        def get_stat(col_name):
            if col_name in col_idx:
                try:
                    val = cells[col_idx[col_name]].text.strip()
                    if val == '-': return 0
                    return int(val) if val.isdigit() else 0
                except IndexError:
                    return 0
            return 0

        players_data.append({
            'ep_id': int(ep_id),
            'playerName': player_name,
            'league': league.upper(),
            'season': season,
            'gamesPlayed': get_stat('gp'),
            'goals': get_stat('g'),
            'assists': get_stat('a'),
            'points': get_stat('tp'),
            'pim': get_stat('pim'),         # New!
            'hits': get_stat('hits'),       # New!
            'blocks': get_stat('blocks'),   # New!
            'profileUrl': profile_url
        })

        count += 1

    return pd.DataFrame(players_data)


# ==========================================
# EXECUTION SCRIPT
# ==========================================

print("--- FETCHING MINOR LEAGUE & NCAA LEADERS ---")
whl_df = scrape_ep_league_leaders("whl", "2025-2026", 50)
time.sleep(3)
ahl_df = scrape_ep_league_leaders("ahl", "2025-2026", 50)
time.sleep(3)
ohl_df = scrape_ep_league_leaders("ohl", "2025-2026", 50)
time.sleep(3)
ncaa_df = scrape_ep_league_leaders("ncaa", "2025-2026", 50) # The New Addition!

# Combine only successful pulls
successful_dfs = [df for df in [whl_df, ahl_df, ohl_df, ncaa_df] if df is not None and not df.empty]

if successful_dfs:
    master_prospects_df = pd.concat(successful_dfs, ignore_index=True)
    print(f"\nSuccessfully scraped {len(master_prospects_df)} top prospects!")

    # Print a quick preview of the new columns
    print(master_prospects_df[['playerName', 'league', 'points', 'pim', 'hits']].head(5))

    table_name = "prospects_baseline"

    # if_exists='replace' means it will recreate the table to include our 3 new columns
    master_prospects_df.to_sql(table_name, con=engine, if_exists='replace', index=False)

    print(f"\nTop rookies safely written to '{table_name}'.")
else:
    print("\nFailed to compile any prospect data.")
