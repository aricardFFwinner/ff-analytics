# -*- coding: utf-8 -*-
"""ESPNリーグデータを取得し、素のdict(スナップショット)に変換する。

espn-apiのオブジェクトを直接引き回さず、後段(分析・レポート)は
このスナップショットだけに依存させる。テスト時はモックJSONを読める。
"""
import os
from ff_config import MY_TEAM_ID, SEASON


def _player_to_dict(p, current_week):
    """espn_api Player → dict"""
    weekly_proj = {}
    weekly_actual = {}
    for period, st in (p.stats or {}).items():
        if period == 0:
            continue
        if "projected_points" in st:
            weekly_proj[int(period)] = round(st.get("projected_points") or 0.0, 2)
        if "points" in st:
            weekly_actual[int(period)] = round(st.get("points") or 0.0, 2)

    opponents = {}
    for wk, game in (p.schedule or {}).items():
        try:
            opponents[int(wk)] = game.get("team", "")
        except (ValueError, TypeError):
            continue

    return {
        "id": getattr(p, "playerId", None),
        "name": getattr(p, "name", "?"),
        "position": getattr(p, "position", "?"),
        "pro_team": getattr(p, "proTeam", "?"),
        "injury_status": getattr(p, "injuryStatus", "") or "",
        "lineup_slot": getattr(p, "lineupSlot", ""),
        "percent_owned": getattr(p, "percent_owned", -1),
        "total_points": getattr(p, "total_points", 0.0),
        "avg_points": getattr(p, "avg_points", 0.0),
        "proj_total": getattr(p, "projected_total_points", 0.0),
        "proj_avg": getattr(p, "projected_avg_points", 0.0),
        "weekly_proj": weekly_proj,
        "weekly_actual": weekly_actual,
        "opponents": opponents,
        "pos_rank": getattr(p, "posRank", 0),
    }


def fetch_snapshot():
    """ESPNからリーグ全体のスナップショットを取得する。"""
    from espn_api.football import League

    league_id = int(os.environ["LEAGUE_ID"].strip())
    espn_s2 = os.environ["ESPN_S2"].strip()
    swid = os.environ["SWID"].strip()
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"

    lg = League(league_id=league_id, year=SEASON, espn_s2=espn_s2, swid=swid)
    current_week = lg.current_week or 1

    teams = []
    for t in lg.teams:
        owner = ""
        if t.owners and isinstance(t.owners, list) and isinstance(t.owners[0], dict):
            owner = t.owners[0].get("firstName", "") or ""
        teams.append({
            "team_id": t.team_id,
            "name": t.team_name,
            "owner": owner,
            "wins": t.wins,
            "losses": t.losses,
            "points_for": round(t.points_for, 1),
            "points_against": round(t.points_against, 1),
            "waiver_rank": getattr(t, "waiver_rank", 0),
            "playoff_pct": round(getattr(t, "playoff_pct", 0.0), 1),
            "standing": getattr(t, "standing", 0),
            "roster": [_player_to_dict(p, current_week) for p in t.roster],
        })

    # FA(各ポジション別に取得して結合)
    fa = {}
    for pos, size in [("QB", 100), ("RB", 100), ("WR", 100), ("TE", 100), ("D/ST", 25), ("K", 40)]:
        try:
            players = lg.free_agents(size=size, position=pos)
        except Exception:
            players = []
        fa[pos] = [_player_to_dict(p, current_week) for p in players]

    return {
        "league_name": lg.settings.name,
        "season": SEASON,
        "current_week": current_week,
        "my_team_id": MY_TEAM_ID,
        "teams": teams,
        "free_agents": fa,
    }
