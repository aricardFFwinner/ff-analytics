# -*- coding: utf-8 -*-
"""P2: モンテカルロシミュレーション(標準ライブラリのみ)。

残りレギュラーシーズンの全対戦を「チーム週次戦力(Bye反映の最適スタメン合計)
+正規乱数」で決着させ、プレーオフ進出%・優勝%・条件付き確率を求める。

プレーオフ形式はESPN設定(playoff_team_count)から読む。
4チーム制: W15=準決勝(1位vs4位, 2位vs3位)、W16+W17合計=決勝/3位決定
2チーム制: W15-17の3週合計で決勝
※実際のリーグ設定が異なる場合はこの前提を要調整(レポートに前提を明記)
"""
import math
import random

from ff_config import (
    REGULAR_SEASON_END, CHAMPIONSHIP_WEEKS, POS_WEEKLY_SD, MC_SIMS,
    PLAYOFF_TEAMS_FALLBACK, STARTER_SLOTS,
)
import analysis


def _team_sigma():
    """スタメン構成からチーム週次得点の標準偏差を合成。"""
    var = 0.0
    for pos, n in STARTER_SLOTS.items():
        var += n * (POS_WEEKLY_SD.get(pos, 8.0) ** 2)
    var += POS_WEEKLY_SD.get("FLEX", 8.3) ** 2  # FLEX枠
    return math.sqrt(var)


def _bye_aware_score(p, week):
    if p.get("bye") == week:
        return 0.0
    s, _ = analysis.player_week_score(p, week)
    return s


def _lineup_total(roster, week):
    """その週の最適スタメン合計(Bye反映。将来週は怪我状態を織り込まない簡略化)。"""
    from ff_config import FLEX_ELIGIBLE
    scored = sorted(((p, _bye_aware_score(p, week)) for p in roster),
                    key=lambda x: x[1], reverse=True)
    total, used = 0.0, set()
    for pos in ("QB", "RB", "WR", "TE", "D/ST", "K"):
        need = STARTER_SLOTS.get(pos, 0)
        for p, s in scored:
            if need == 0:
                break
            if id(p) in used or p.get("position") != pos:
                continue
            total += s
            used.add(id(p))
            need -= 1
    for p, s in scored:  # FLEX
        if id(p) not in used and p.get("position") in FLEX_ELIGIBLE:
            total += s
            break
    return total


def run(snapshot, week, sims=MC_SIMS, seed=20260817):
    """モンテカルロ本体。matchupsが無いスナップショットではNoneを返す。

    返り値: {"teams": {tid: {"playoff_pct","champ_pct","avg_wins","seed_dist"}},
             "conditional": {"win": champ%, "lose": champ%} (自チーム・今週ありの場合),
             "playoff_teams": N, "sims": N, "sigma": σ, "assumption": 説明文}
    """
    teams = snapshot["teams"]
    if not all(t.get("matchups") for t in teams):
        return None
    my_id = snapshot["my_team_id"]
    playoff_teams = (snapshot.get("settings") or {}).get("playoff_team_count") \
        or PLAYOFF_TEAMS_FALLBACK
    playoff_teams = min(playoff_teams, len(teams))
    sigma = _team_sigma()
    rng = random.Random(seed)

    tids = [t["team_id"] for t in teams]
    idx = {tid: i for i, tid in enumerate(tids)}
    n = len(tids)

    # 既知の状態(実績)
    base_wins = [0] * n
    base_pf = [0.0] * n
    for t in teams:
        i = idx[t["team_id"]]
        base_wins[i] = t.get("wins", 0)
        base_pf[i] = float(t.get("points_for", 0.0))

    # 将来週の平均得点(チーム×週)
    remaining_reg = [w for w in range(max(week, 1), REGULAR_SEASON_END + 1)]
    means = {}
    for t in teams:
        i = idx[t["team_id"]]
        for w in remaining_reg + CHAMPIONSHIP_WEEKS:
            means[(i, w)] = _lineup_total(t["roster"], w)

    # 対戦表(レギュラー残り): [(week, i, j)] 重複除去
    games = []
    seen = set()
    for t in teams:
        i = idx[t["team_id"]]
        for w in remaining_reg:
            opp = t["matchups"].get(w) or t["matchups"].get(str(w))
            if opp is None:
                continue
            j = idx.get(opp)
            if j is None:
                continue
            key = (w, min(i, j), max(i, j))
            if key not in seen:
                seen.add(key)
                games.append((w, min(i, j), max(i, j)))

    po_count = [0] * n
    champ_count = [0] * n
    seed_dist = [[0] * (playoff_teams + 1) for _ in range(n)]
    wins_sum = [0.0] * n
    my_i = idx[my_id]
    cond = {"win": [0, 0], "lose": [0, 0]}  # [優勝回数, 分母]

    g = rng.gauss
    for _ in range(sims):
        wins = base_wins[:]
        pf = base_pf[:]
        my_week_result = None
        for (w, i, j) in games:
            si = g(means[(i, w)], sigma)
            sj = g(means[(j, w)], sigma)
            pf[i] += si
            pf[j] += sj
            if si >= sj:
                wins[i] += 1
            else:
                wins[j] += 1
            if w == week and (i == my_i or j == my_i):
                my_week_result = "win" if ((si >= sj) == (i == my_i)) else "lose"
        order = sorted(range(n), key=lambda k: (-wins[k], -pf[k]))
        po = order[:playoff_teams]
        for rank, k in enumerate(order):
            wins_sum[k] += wins[k]
            if rank < playoff_teams:
                po_count[k] += 1
                seed_dist[k][rank + 1] += 1

        # チャンピオンシップ
        if playoff_teams >= 4:
            w15 = CHAMPIONSHIP_WEEKS[0]
            s1 = g(means[(po[0], w15)], sigma)
            s4 = g(means[(po[3], w15)], sigma)
            s2 = g(means[(po[1], w15)], sigma)
            s3 = g(means[(po[2], w15)], sigma)
            f1 = po[0] if s1 >= s4 else po[3]
            f2 = po[1] if s2 >= s3 else po[2]
        else:
            f1, f2 = po[0], po[1]
        fw = CHAMPIONSHIP_WEEKS[1:] if playoff_teams >= 4 else CHAMPIONSHIP_WEEKS
        t1 = sum(g(means[(f1, w)], sigma) for w in fw)
        t2 = sum(g(means[(f2, w)], sigma) for w in fw)
        champ = f1 if t1 >= t2 else f2
        champ_count[champ] += 1
        if my_week_result:
            cond[my_week_result][1] += 1
            if champ == my_i:
                cond[my_week_result][0] += 1

    result = {"teams": {}, "sims": sims, "playoff_teams": playoff_teams,
              "sigma": round(sigma, 1)}
    for t in teams:
        i = idx[t["team_id"]]
        result["teams"][t["team_id"]] = {
            "playoff_pct": round(100.0 * po_count[i] / sims, 1),
            "champ_pct": round(100.0 * champ_count[i] / sims, 1),
            "avg_wins": round(wins_sum[i] / sims, 1),
            "seed_dist": {s: round(100.0 * c / sims, 1)
                          for s, c in enumerate(seed_dist[i]) if s >= 1 and c > 0},
        }
    if cond["win"][1] > 0 and cond["lose"][1] > 0:
        result["conditional"] = {
            "win": round(100.0 * cond["win"][0] / cond["win"][1], 1),
            "lose": round(100.0 * cond["lose"][0] / cond["lose"][1], 1),
        }
    result["assumption"] = (
        f"{playoff_teams}チーム制"
        + ("(W15準決勝、W16+17合計で決勝)" if playoff_teams >= 4
           else "(W15-17の3週合計で決勝)")
        + f"、σ={result['sigma']}pt/週、怪我の将来発生は非考慮")
    return result
