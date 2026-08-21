# -*- coding: utf-8 -*-
"""P2完成版: トレードジェネレーター + 競合カード/ブロック警戒。

評価はブレンド値(analysis.player_value_ppg)ベース。
- 1対1と2対1(双方向)を列挙し、双方のスタメン合計の変化を
  「残シーズン平均」「W15-17平均」の2軸で算出
- ◎win-win(双方プラス、かつΔ優勝%を算出できた場合はそれも正) / ○要交渉(自分プラス、相手の損失が閾値以内)
- 2対1/1対2はロスター枠の過不足を補正(空いた枠には実際に不足したポジションの
  FAプール最良=実在の選手を充当。溢れた枠は最弱を仮想ドロップ)
- 上位案はロスター入替後の再シミュレーションでΔ優勝確率を算出
  (同一シードの共通乱数で差分のノイズを抑制)。
  再シミュレーションもスコアリングと同じ「双方FA補強後」の土俵で行う
  (_apply_fa_upgradesをbase/modified双方・全チームに適用)。
  |Δ優勝%|がTRADE_CHAMP_NOISE_FLOOR未満はノイズと区別できないため、
  それを明確に下回る(=大きくマイナス)案は候補から除外し、
  残りはΔ優勝%優先で並べ替える。
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
FA_POOL_LOOKAHEAD = 20         # 枠paddingで各ポジションを見るFA候補数(_fa_poolのtop_n=3とは別)
TRADE_CHAMP_NOISE_FLOOR = 2.0  # |Δ優勝%|がこれ未満は共通乱数ノイズと見分けがつかない値として扱う


def _val(p):
    return analysis.player_value_ppg(p)


def _team_metrics(roster, week):
    """(残シーズン平均pt, W15-17平均pt)。"""
    reg = [w for w in range(max(week, 1), REGULAR_SEASON_END + 1)] or [REGULAR_SEASON_END]
    ros = sum(simulate._lineup_total(roster, w) for w in reg) / len(reg)
    champ = sum(simulate._lineup_total(roster, w) for w in CHAMPIONSHIP_WEEKS) / len(CHAMPIONSHIP_WEEKS)
    return ros, champ


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
    """raw_faに候補が1人もいない場合の最終フォールバックのみに使う。"""
    return {"name": "(FA補充)", "position": "WR", "pro_team": "-",
            "proj_avg": value, "weekly_proj": {}, "bye": None}


def _fa_candidates(raw_fa, pos, exclude_names):
    return [p for p in (raw_fa or {}).get(pos, [])[:FA_POOL_LOOKAHEAD]
            if p["name"] not in exclude_names]


def _position_shortfall(orig_roster, merged_roster):
    """トレード後(枠調整前)のロスターが元のロスターと比べて
    どのポジションで何人減っているか({pos: 不足数}。減っていないポジションは含めない)。
    """
    orig_cnt, new_cnt = {}, {}
    for p in orig_roster:
        if p["position"] in OFFENSE_POS:
            orig_cnt[p["position"]] = orig_cnt.get(p["position"], 0) + 1
    for p in merged_roster:
        if p["position"] in OFFENSE_POS:
            new_cnt[p["position"]] = new_cnt.get(p["position"], 0) + 1
    return {pos: orig_cnt.get(pos, 0) - new_cnt.get(pos, 0) for pos in OFFENSE_POS
            if orig_cnt.get(pos, 0) > new_cnt.get(pos, 0)}


def _adjust_size(orig_roster, merged_roster, target_len, raw_fa):
    """トレード後のロスター枠を補正。

    不足→実際に空いたポジションのFA最良(raw_fa=snapshot["free_agents"]の実選手。
    weekly_proj/byeも本物)を充当。そのポジションにFAがいなければ全ポジション最良に
    フォールバック、それも無ければ最終手段として仮想FA(価値0)を置く。
    超過→最弱を仮想ドロップ(従来通り)。
    """
    r = list(merged_roster)
    need = target_len - len(r)
    if need > 0:
        shortfall = sorted(_position_shortfall(orig_roster, r).items(), key=lambda kv: -kv[1])
        used = {p["name"] for p in r}
        for _ in range(need):
            fa = None
            for pos, _n in shortfall:
                cands = _fa_candidates(raw_fa, pos, used)
                if cands:
                    fa = max(cands, key=_val)
                    break
            if fa is None:
                for pos in FLEX_GROUP:
                    for cand in _fa_candidates(raw_fa, pos, used):
                        if fa is None or _val(cand) > _val(fa):
                            fa = cand
            if fa is None:
                fa = _virtual_fa(0.0)
            r.append(fa)
            used.add(fa["name"])
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
    raw_fa = snapshot.get("free_agents")  # 枠paddingで実選手を引くための生FAプール(_fa_poolとは別物)
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
            my_merged = [p for p in me["roster"] if p["name"] not in give_names] + get
            opp_merged = [p for p in opp["roster"] if p["name"] not in get_names] + give
            my_new = _adjust_size(me["roster"], my_merged, len(me["roster"]), raw_fa)
            opp_new = _adjust_size(opp["roster"], opp_merged, len(opp["roster"]), raw_fa)

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
        # 修正1: 再シミュレーションもスコアリングと同じ「双方FA補強後」の土俵で行う。
        # base側も全チームに_apply_fa_upgradesを適用してから走らせる(土俵を揃える)。
        # 同一FAを複数チームに重複して割り当てる簡略化はスコアリング側の既存挙動を踏襲。
        base_teams = []
        for t in teams:
            t2 = dict(t)
            t2["roster"] = _apply_fa_upgrades(t["roster"], fa_pool)
            base_teams.append(t2)
        base_snapshot = dict(snapshot)
        base_snapshot["teams"] = base_teams
        base = simulate.run(base_snapshot, week, sims=TRADE_RESIM_SIMS)
        base_champ = base["teams"][me["team_id"]]["champ_pct"] if base else None
        for c in candidates[:TRADE_TOP_RESIM]:
            if base_champ is None:
                break
            mod_teams = []
            for t in teams:
                t2 = dict(t)
                if t["team_id"] == me["team_id"]:
                    t2["roster"] = _apply_fa_upgrades(c["my_new_roster"], fa_pool)
                elif t["team_id"] == c["opp_team"]["team_id"]:
                    get_names = {p["name"] for p in c["get"]}
                    opp_merged = [p for p in t["roster"] if p["name"] not in get_names] + c["give"]
                    opp_new = _adjust_size(t["roster"], opp_merged, len(t["roster"]), raw_fa)
                    t2["roster"] = _apply_fa_upgrades(opp_new, fa_pool)
                else:
                    t2["roster"] = _apply_fa_upgrades(t["roster"], fa_pool)
                mod_teams.append(t2)
            mod = dict(snapshot)
            mod["teams"] = mod_teams
            r = simulate.run(mod, week, sims=TRADE_RESIM_SIMS)
            if r:
                c["d_champ_pct"] = round(
                    r["teams"][me["team_id"]]["champ_pct"] - base_champ, 1)
                if c["grade"] == "◎" and c["d_champ_pct"] <= 0:
                    c["grade"] = "○"  # 修正3: ◎はΔ優勝%も正であることが条件(算出済みの場合のみ)

        # 修正2: Δ優勝%が算出済みの案は、ノイズ下限(TRADE_CHAMP_NOISE_FLOOR)を明確に
        # 下回るものを落とし、残りはΔ優勝%を最優先の並び順にする
        # (算出できなかった案は末尾に残す)。
        candidates = [c for c in candidates
                      if c.get("d_champ_pct") is None or c["d_champ_pct"] >= -TRADE_CHAMP_NOISE_FLOOR]
        candidates.sort(key=lambda c: (
            c["grade"] != "◎",
            c.get("d_champ_pct") is None,
            -(c["d_champ_pct"] if c.get("d_champ_pct") is not None else 0.0),
            -(c["d_my_ros"] + 0.5 * c["d_my_champ"]),
        ))
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
