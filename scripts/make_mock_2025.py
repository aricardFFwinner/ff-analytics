# -*- coding: utf-8 -*-
"""検証用: 2025年実データから「ESPN風モックスナップショット」を作る。

事前期待 = 2024年実PPG(前年実績。ESPN季節予測の代役)。
6チーム×14人を期待順にスネークドラフトし、残りをFAにする。
使い方: python3 make_mock_2025.py <upto_week> <出力パス>
"""
import csv
import json
import sys
import os

CACHE = os.environ.get("OPP_CACHE_DIR", "/tmp/oppcache")
POS = {"QB", "RB", "WR", "TE"}


def season_ppg(path):
    agg = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("season_type") != "REG" or row.get("position") not in POS:
                continue
            key = row["player_id"]
            rec = agg.setdefault(key, {"name": row.get("player_display_name"),
                                       "pos": row["position"], "team": row.get("team"),
                                       "pts": 0.0, "g": 0})
            rec["pts"] += float(row.get("fantasy_points_ppr") or 0)
            rec["g"] += 1
            rec["team"] = row.get("team")
    return {k: {**v, "ppg": round(v["pts"] / v["g"], 2)} for k, v in agg.items() if v["g"] >= 3}


def weekly_actual_2025(path, upto_week):
    wa = {}
    info = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("season_type") != "REG" or row.get("position") not in POS:
                continue
            wk = int(row["week"])
            if wk > upto_week:
                continue
            pid = row["player_id"]
            wa.setdefault(pid, {})[wk] = float(row.get("fantasy_points_ppr") or 0)
            info[pid] = {"name": row.get("player_display_name"),
                         "pos": row["position"], "team": row.get("team")}
    return wa, info


def main():
    upto_week = int(sys.argv[1])
    out = sys.argv[2]
    exp = season_ppg(os.path.join(CACHE, "stats_player_week_2024.csv"))
    wa25, info25 = weekly_actual_2025(os.path.join(CACHE, "stats_player_week_2025.csv"), upto_week)

    # 2025年に出場実績のある選手 + 2024実績のみの選手をプール化
    pool = {}
    for pid, i in info25.items():
        e = exp.get(pid)
        pool[pid] = {
            "id": pid, "name": i["name"], "position": i["pos"], "pro_team": i["team"],
            "injury_status": "ACTIVE", "lineup_slot": "", "percent_owned": -1,
            "proj_avg": e["ppg"] if e else 0.0,
            "proj_total": round((e["ppg"] if e else 0.0) * 17, 1),
            "weekly_proj": {}, "weekly_actual": {str(k): v for k, v in wa25.get(pid, {}).items()},
            "opponents": {}, "is_rookie": False,
            "total_points": round(sum(wa25.get(pid, {}).values()), 1),
            "avg_points": 0.0, "pos_rank": 0,
        }

    ranked = sorted(pool.values(), key=lambda p: p["proj_avg"], reverse=True)

    # スネークドラフト: 6チーム×14人。ポジション上限(QB2/TE2)で現実に寄せる
    teams = [{"team_id": i + 1, "name": f"Team{i+1}", "owner": f"GM{i+1}",
              "wins": 0, "losses": 0, "points_for": 0.0, "points_against": 0.0,
              "waiver_rank": i + 1, "playoff_pct": 0.0, "standing": i + 1, "roster": []}
             for i in range(6)]
    drafted = set()
    order = list(range(6))
    rnd = 0
    while any(len(t["roster"]) < 14 for t in teams):
        seq = order if rnd % 2 == 0 else order[::-1]
        for ti in seq:
            t = teams[ti]
            if len(t["roster"]) >= 14:
                continue
            cnt = {}
            for p in t["roster"]:
                cnt[p["position"]] = cnt.get(p["position"], 0) + 1
            for p in ranked:
                if p["id"] in drafted:
                    continue
                if p["position"] == "QB" and cnt.get("QB", 0) >= 2:
                    continue
                if p["position"] == "TE" and cnt.get("TE", 0) >= 2:
                    continue
                t["roster"].append(p)
                drafted.add(p["id"])
                break
        rnd += 1

    fa = {pos: [] for pos in ("QB", "RB", "WR", "TE")}
    for p in ranked:
        if p["id"] not in drafted and len(fa[p["position"]]) < 100:
            fa[p["position"]].append(p)
    # 2025年出場済みで2024実績ゼロ(=無名)の選手もFA末尾に含まれていることを確認
    snapshot = {
        "league_name": "MOCK Outsidrs_FFNFL (2025 as-of検証)",
        "season": 2025, "current_week": upto_week + 1, "my_team_id": 1,
        "teams": teams, "free_agents": fa,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    print(f"mock: teams=6x14, FA: " + ", ".join(f"{k}:{len(v)}" for k, v in fa.items()))


if __name__ == "__main__":
    main()
