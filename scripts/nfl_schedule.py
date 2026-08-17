# -*- coding: utf-8 -*-
"""nflverseのスケジュールデータからBye週・対戦相手・会場情報を取得する。"""
import csv
import io
import urllib.request

from ff_config import SEASON, ESPN_TO_NFLVERSE

GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"


def to_nflverse_abbrev(espn_abbrev: str) -> str:
    return ESPN_TO_NFLVERSE.get(espn_abbrev, espn_abbrev)


def fetch_schedule_info():
    """{team_abbrev(nflverse): {"bye": int, "games": {week: {...}}}} を返す。"""
    with urllib.request.urlopen(GAMES_URL, timeout=60) as r:
        text = r.read().decode("utf-8", errors="replace")

    games_by_team = {}
    reg_weeks = set()
    for row in csv.DictReader(io.StringIO(text)):
        if row["season"] != str(SEASON) or row["game_type"] != "REG":
            continue
        week = int(row["week"])
        reg_weeks.add(week)
        for side, opp_side in (("home_team", "away_team"), ("away_team", "home_team")):
            team = row[side]
            games_by_team.setdefault(team, {"games": {}})
            games_by_team[team]["games"][week] = {
                "opponent": row[opp_side],
                "is_home": side == "home_team",
                "home_team": row["home_team"],
                "gameday": row.get("gameday", ""),
                "weekday": row.get("weekday", ""),
                "gametime": row.get("gametime", ""),
                "location": row.get("location", ""),
                "roof": row.get("roof", ""),
                "surface": row.get("surface", ""),
                "stadium": row.get("stadium", ""),
            }

    weeks_1_to_bye_max = [w for w in sorted(reg_weeks) if w <= 14] or list(range(1, 15))
    for team, info in games_by_team.items():
        byes = [w for w in weeks_1_to_bye_max if w not in info["games"]]
        info["bye"] = byes[0] if byes else None
    return games_by_team
