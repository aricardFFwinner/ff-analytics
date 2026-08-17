# -*- coding: utf-8 -*-
"""nflverseの追加データ: ルーキー判定と、DST向けの相手オフェンス指標。"""
import csv
import io
import re
import urllib.request

ROSTER_URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{season}.csv"
STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"

_SUFFIXES = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$")


def norm_name(name: str) -> str:
    n = (name or "").lower().strip()
    n = _SUFFIXES.sub("", n)
    return re.sub(r"[^a-z]", "", n)


def _download(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_rookies(season: int):
    """当年ロスターから entry_year==season の選手(=NFL1年目)の正規化名集合を返す。"""
    text = _download(ROSTER_URL.format(season=season))
    rookies = set()
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("entry_year") == str(season):
            rookies.add(norm_name(row.get("full_name", "")))
    return rookies


def fetch_offense_metrics(season: int):
    """各チームのオフェンス指標(DSTのマッチアップ判断用)を返す。

    {team: {"games": n, "sk_g": 被サック/試合, "to_g": ギブアウェイ/試合}}
    ギブアウェイ = 被インターセプト + ファンブルロスト
    """
    text = _download(STATS_URL.format(season=season))
    reader = csv.DictReader(io.StringIO(text))
    int_col = None
    agg = {}  # team -> {week: {"sk":x,"to":y}}
    for row in reader:
        if row.get("season_type") != "REG":
            continue
        if int_col is None:
            for cand in ("passing_interceptions", "interceptions"):
                if cand in row:
                    int_col = cand
                    break
            int_col = int_col or "passing_interceptions"
        team = row.get("team")
        week = row.get("week")
        if not team or not week:
            continue

        def num(col):
            v = row.get(col)
            try:
                return float(v) if v not in (None, "", "NA") else 0.0
            except ValueError:
                return 0.0

        wk = agg.setdefault(team, {}).setdefault(week, {"sk": 0.0, "to": 0.0})
        wk["sk"] += num("sacks_suffered")
        wk["to"] += num(int_col) + num("fumbles_lost_total")

    result = {}
    for team, weeks in agg.items():
        games = len(weeks)
        if games == 0:
            continue
        sk = sum(w["sk"] for w in weeks.values())
        to = sum(w["to"] for w in weeks.values())
        result[team] = {"games": games,
                        "sk_g": round(sk / games, 1),
                        "to_g": round(to / games, 1)}
    return result
