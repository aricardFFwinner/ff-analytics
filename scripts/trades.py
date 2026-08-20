# -*- coding: utf-8 -*-
"""P2完成版: トレードジェネレーター + 競合カード/ブロック警戒。

評価はブレンド値(analysis.player_value_ppg)ベース。
- 1対1と2対1(双方向)を列挙し、双方のスタメン合計の変化を
  「残シーズン平均」「W15-17平均」の2軸で算出
- ◎win-win(双方プラス) / ○要交渉(自分プラス、相手の損失が閾値以内)
- 2対1はロスター枠の過不足を補正(空いた枠にはFA最良を仮想充当、
  溢れた枠は最弱を仮想ドロップ)
- 上位案はロスター入替後の再シミュレーションでΔ優勝確率を算出
  (同一シードの共通乱数で差分のノイズを抑制)
"""
import itertools

import analysis
import simulate
from ff_config import (
    REGULAR_SEASON_END, CHAMPIONSHIP_WEEKS,
    TRADE_OPP_MAX_LOSS, TRADE_TOP_RESIM, TRADE_RESIM_SIMS, TRADE_MAX_SHOWN,
    ROOKIE_RULE_UNTIL_WEEK, ROOKIE_MIN_COUNT,
)

OFFENSE_POS = ("QB", "RB", "WR", "TE")
FLEX_GROUP = ("RB", "WR", "TE")


def _val(p):
    return analysis.player_value_ppg(p)


def _team_metrics(roster, week):
    """(残シーズン平均pt, W15-17平均pt)。"""
    reg = [w for w in range(max(week, 1), REGULAR_SEASON_END + 1)] or [REGULAR_SEASON_END]
    ros = sum(simulate._lineup_total(roster, w) for w in reg) / len(reg)
    champ = sum(simulate._lineup_total(roster, w) for w in CHAMPIONSHIP_WEEKS) / len(CHAMPIONSHIP_WEEKS)
    return ros, champ


def _best_fa_value(snapshot):
    """FA最良のブレンド値(FLEX系)。2対1で空いた枠の代替価値に使う。"""
    best = 0.0
    for pos in FLEX_GROUP:
        for p in (snapshot.get("free_agents") or {}).get(pos, [])[:20]:
            best = max(best, _val(p))
    return best


def _fa_pool(snapshot, top_n=3):
    """ポジション別のFA上位(FA代替の材料)。"""
    pool = {}
    for pos in OFFENSE_POS:
        players = sorted((snapshot.get("free_agents") or {}).get(pos, [])[:25],
                         key=_val, reverse=True)
        pool[pos] = players[:top_n]
    return pool


def _apply_fa_upgrades(roster, fa_pool):
    """FAで実現できる最良形に整えたロスターを返す(仮想)。

    トレード評価の基準線をこれにすることで「FAでタダで埋まる改善」を
    トレードの手柄から差し引く(機会費用の反映)。
    同ポジションの最弱選手よりFA候補が上なら入れ替える。
    """
    r = list(roster)
    used = set()
    for pos in OFFENSE_POS:
        for fa in fa_pool.get(pos, []):
            fid = id(fa)
            if fid in used:
                continue
            same = [p for p in r if p.get("position") == pos]
            if not same:
                r.append(fa)
                used.add(fid)
                continue
            worst = min(same, key=_val)
            if _val(fa) > _val(worst) + 0.01:
                r[r.index(worst)] = fa
                used.add(fid)
    return r


def _virtual_fa(value):
    return {"name": "(FA補充)", "position": "WR", "pro_team": "-",
            "proj_avg": value, "weekly_proj": {}, "bye": None}


def _adjust_size(roster, target_len, fa_value):
    """トレード後のロスター枠を補正。不足→FA最良を仮想充当 / 超過→最弱を仮想ドロップ。"""
    r = list(roster)
    while len(r) < target_len:
        r.append(_virtual_fa(fa_value))
    while len(r) > target_len:
        r.remove(min(r, key=_val))
    return r


def _protected_names(team, week):
    """トレードに出せない選手(ルーキー保持ルール)。"""
    rookies = [p for p in team["roster"] if p.get("is_rookie")]
    if week <= ROOKIE_RULE_UNTIL_WEEK and len(rookies) <= ROOKIE_MIN_COUNT:
        return {p["name"] for p in rookies}
    return set()


def _pitch(opp_team, get_players, give_players, surplus_map, sos_map):
    """相手への売り文句を機械生成。"""
    L = []
    defc = surplus_map.get(opp_team["team_id"], {}).get("deficit", [])
    for p in give_players:  # 相手が受け取る選手
        if p["position"] in defc:
            L.append(f"{p['position']}の穴が埋まる")
        o = p.get("opp") or {}
        if o.get("trend", "").startswith("↗"):
            L.append(f"{p['name']}は機会が上昇中")
        sos = (sos_map.get(opp_team["team_id"]) or {}).get(p["position"])
        if sos is not None and sos <= 12:
            L.append(f"W15-17の{p['position']}日程が楽")
    if len(get_players) > len(give_players):
        L.append("ロスター枠が1つ空く(waiver機動力up)")
    seen, out = set(), []
    for x in L:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return "、".join(out[:3]) or "純粋な価値交換"


def generate_trades(snapshot, week, surplus, sos):
    """トレード候補を生成して返す(自チーム視点)。"""
    teams = snapshot["teams"]
    me = next(t for t in teams if t["team_id"] == snapshot["my_team_id"])
    others = [t for t in teams if t["team_id"] != snapshot["my_team_id"]]
    fa_value = _best_fa_value(snapshot)
    surplus_map = {r["team_id"]: r for r in surplus["rows"]}
    sos_map = {r["team_id"]: r["by_pos"] for r in (sos or {}).get("rows", [])} if sos else {}

    # 基準線=「FAで補強し尽くした後」の戦力。FAで埋まる改善はトレードの手柄にしない
    fa_pool = _fa_pool(snapshot)
    my_base_ros, my_base_champ = _team_metrics(
        _apply_fa_upgrades(me["roster"], fa_pool), week)
    protected = _protected_names(me, week)
    my_tradeable = [p for p in me["roster"]
                    if p["position"] in OFFENSE_POS and p["name"] not in protected]

    candidates = []
    for opp in others:
        opp_base_ros, opp_base_champ = _team_metrics(
            _apply_fa_upgrades(opp["roster"], fa_pool), week)
        opp_tradeable = [p for p in opp["roster"] if p["position"] in OFFENSE_POS]

        combos = []
        # 1対1
        for g in my_tradeable:
            for r in opp_tradeable:
                if abs(_val(g) - _val(r)) <= 10:
                    combos.append(([g], [r]))
        # 2対1(自分が2出して1貰う=枠が空く) / 1対2(2貰う=枠が埋まる)
        for g1, g2 in itertools.combinations(my_tradeable, 2):
            for r in opp_tradeable:
                if abs(_val(g1) + _val(g2) - _val(r)) <= 12:
                    combos.append(([g1, g2], [r]))
        for g in my_tradeable:
            for r1, r2 in itertools.combinations(opp_tradeable, 2):
                if abs(_val(g) - _val(r1) - _val(r2)) <= 12:
                    combos.append(([g], [r1, r2]))

        evals = 0
        for give, get in combos:
            if evals >= 400:
                break
            evals += 1
            give_names = {p["name"] for p in give}
            get_names = {p["name"] for p in get}
            my_new = [p for p in me["roster"] if p["name"] not in give_names] + get
            opp_new = [p for p in opp["roster"] if p["name"] not in get_names] + give
            my_new = _adjust_size(my_new, len(me["roster"]), fa_value)
            opp_new = _adjust_size(opp_new, len(opp["roster"]), fa_value)

            # トレード後も双方FA補強できる前提で評価(基準線と同じ土俵)
            my_ros, my_champ = _team_metrics(_apply_fa_upgrades(my_new, fa_pool), week)
            opp_ros, opp_champ = _team_metrics(_apply_fa_upgrades(opp_new, fa_pool), week)
            d_my_ros = my_ros - my_base_ros
            d_my_champ = my_champ - my_base_champ
            d_opp_ros = opp_ros - opp_base_ros
            d_opp_champ = opp_champ - opp_base_champ

            if d_my_ros <= 0.3 and d_my_champ <= 0.3:
                continue  # FA代替で埋まる程度の改善は提示しない
            winwin = d_opp_ros > 0.05 or d_opp_champ > 0.05
            if not winwin and min(d_opp_ros, d_opp_champ) < -TRADE_OPP_MAX_LOSS:
                continue
            candidates.append({
                "opp_team": opp, "give": give, "get": get,
                "grade": "◎" if winwin else "○",
                "d_my_ros": round(d_my_ros, 1), "d_my_champ": round(d_my_champ, 1),
                "d_opp_ros": round(d_opp_ros, 1), "d_opp_champ": round(d_opp_champ, 1),
                "pitch": _pitch(opp, get, give, surplus_map, sos_map),
                "my_new_roster": my_new,
            })

    candidates.sort(key=lambda c: (c["grade"] != "◎",
                                   -(c["d_my_ros"] + 0.5 * c["d_my_champ"])))
    candidates = candidates[:TRADE_MAX_SHOWN]

    # 上位案のΔ優勝確率(共通乱数で差分のノイズを抑える)
    if candidates and all(t.get("matchups") for t in teams):
        base = simulate.run(snapshot, week, sims=TRADE_RESIM_SIMS)
        base_champ = base["teams"][me["team_id"]]["champ_pct"] if base else None
        for c in candidates[:TRADE_TOP_RESIM]:
            if base_champ is None:
                break
            mod_teams = []
            for t in teams:
                t2 = dict(t)
                if t["team_id"] == me["team_id"]:
                    t2["roster"] = c["my_new_roster"]
                elif t["team_id"] == c["opp_team"]["team_id"]:
                    get_names = {p["name"] for p in c["get"]}
                    t2["roster"] = _adjust_size(
                        [p for p in t["roster"] if p["name"] not in get_names] + c["give"],
                        len(t["roster"]), _best_fa_value(snapshot))
                mod_teams.append(t2)
            mod = dict(snapshot)
            mod["teams"] = mod_teams
            r = simulate.run(mod, week, sims=TRADE_RESIM_SIMS)
            if r:
                c["d_champ_pct"] = round(
                    r["teams"][me["team_id"]]["champ_pct"] - base_champ, 1)
    for c in candidates:
        c.pop("my_new_roster", None)
    return candidates


# ------------------------------------------------------------------
# 競合カード + ブロック警戒
# ------------------------------------------------------------------

def competitor_cards(snapshot, week, sim, surplus, sos, history):
    """自分以外の5チームのカード情報+ブロック警戒を返す。"""
    teams = snapshot["teams"]
    my_id = snapshot["my_team_id"]
    surplus_map = {r["team_id"]: r for r in surplus["rows"]}
    sos_map = {r["team_id"]: r["by_pos"] for r in (sos or {}).get("rows", [])} if sos else {}
    can_sim = bool(sim) and all(t.get("matchups") for t in teams)
    base = simulate.run(snapshot, week, sims=TRADE_RESIM_SIMS) if can_sim else None

    cards = []
    for t in teams:
        if t["team_id"] == my_id:
            continue
        tid = t["team_id"]
        sd = surplus_map.get(tid, {})
        deficits = sd.get("deficit", [])

        # ブロック警戒: 弱点ポジションを埋める最良FA(いなければ全ポジ最良)
        watch = []
        pool_pos = deficits or list(OFFENSE_POS)
        best_fa = None
        for pos in pool_pos:
            for p in (snapshot.get("free_agents") or {}).get(pos, [])[:15]:
                if best_fa is None or _val(p) > _val(best_fa):
                    best_fa = p
        if best_fa is not None and _val(best_fa) > 0:
            entry = {"fa": best_fa, "d_champ_pct": None}
            if base:
                mod_teams = []
                for t2 in teams:
                    t3 = dict(t2)
                    if t2["team_id"] == tid:
                        worst = min(
                            (p for p in t2["roster"] if p["position"] in OFFENSE_POS),
                            key=_val, default=None)
                        r = [p for p in t2["roster"] if p is not worst] + [best_fa]
                        t3["roster"] = r
                    mod_teams.append(t3)
                mod = dict(snapshot)
                mod["teams"] = mod_teams
                r = simulate.run(mod, week, sims=TRADE_RESIM_SIMS)
                if r:
                    entry["d_champ_pct"] = round(
                        r["teams"][tid]["champ_pct"] - base["teams"][tid]["champ_pct"], 1)
            watch.append(entry)

        trend = []
        for w in sorted(int(x) for x in history.keys()):
            e = history.get(str(w), {}).get("teams", {}).get(str(tid), {})
            if e.get("champ_pct") is not None:
                trend.append((w, e["champ_pct"]))
        cards.append({
            "team": t,
            "surplus": sd.get("surplus", []),
            "deficit": deficits,
            "sos": sos_map.get(tid, {}),
            "sim": (sim or {}).get("teams", {}).get(tid, {}),
            "trend": trend[-5:],
            "watch": watch,
        })
    cards.sort(key=lambda c: -(c["sim"].get("champ_pct") or 0))
    return cards
