# -*- coding: utf-8 -*-
"""The Odds APIからスプレッド/トータルを取得し、チームごとの
インプライドトータル(ブックメーカー予想得点)を計算する。

ODDS_API_KEY が未設定なら {} を返す(レポート側は列を非表示)。
リクエスト消費: 1回の呼び出しで markets 2種 × region 1 = 2クレジット。
"""
import json
import os
import urllib.request

ODDS_URL = ("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
            "?regions=us&markets=spreads,totals&oddsFormat=american&apiKey={key}")

TEAM_NAME_TO_ABBREV = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def fetch_implied_totals():
    """{nflverse略称: {"implied": float, "ou": float, "spread": float}} を返す。"""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        return {}
    try:
        with urllib.request.urlopen(ODDS_URL.format(key=key), timeout=60) as r:
            events = json.loads(r.read().decode())
    except Exception as e:
        print(f"[warn] オッズ取得失敗(スキップ): {e}")
        return {}

    result = {}
    for ev in events:
        try:
            home = TEAM_NAME_TO_ABBREV.get(ev.get("home_team", ""))
            away = TEAM_NAME_TO_ABBREV.get(ev.get("away_team", ""))
            if not home or not away:
                continue
            totals, spreads_home = [], []
            for bm in ev.get("bookmakers", []):
                for mk in bm.get("markets", []):
                    if mk["key"] == "totals":
                        for o in mk.get("outcomes", []):
                            if o.get("name") == "Over" and o.get("point") is not None:
                                totals.append(float(o["point"]))
                    elif mk["key"] == "spreads":
                        for o in mk.get("outcomes", []):
                            if o.get("name") == ev.get("home_team") and o.get("point") is not None:
                                spreads_home.append(float(o["point"]))
            if not totals or not spreads_home:
                continue
            ou = sum(totals) / len(totals)
            sp_home = sum(spreads_home) / len(spreads_home)
            home_implied = (ou - sp_home) / 2.0
            away_implied = ou - home_implied
            for team, implied, spread in ((home, home_implied, sp_home), (away, away_implied, -sp_home)):
                prev = result.get(team)
                # 同一チームが複数イベントに出る場合は直近(先に来た)を優先
                if prev is None:
                    result[team] = {"implied": round(implied, 1), "ou": round(ou, 1),
                                    "spread": round(spread, 1)}
        except Exception:
            continue
    print(f"[info] オッズ取得: {len(result)}チーム分のインプライドトータル")
    return result
