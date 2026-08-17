# -*- coding: utf-8 -*-
"""スナップショットからスタメン推奨・FA推奨・Bye週マップ等を計算する。"""
from ff_config import (
    STARTER_SLOTS, FLEX_ELIGIBLE, INJURY_OUT, INJURY_RISK,
    CLOSE_CALL_MARGIN, CHAMPIONSHIP_WEEKS,
)


def player_week_score(p, week):
    """その週の期待ポイント。週次プロジェクションがあれば優先、なければ季節平均。"""
    wp = p.get("weekly_proj") or {}
    v = wp.get(week) or wp.get(str(week))
    if v is not None and v > 0:
        return float(v), "week"
    if p.get("proj_avg"):
        return float(p["proj_avg"]), "season_avg"
    # 開幕前などavgが無い場合は総予測/14週で近似
    if p.get("proj_total"):
        return float(p["proj_total"]) / 14.0, "season_total/14"
    return 0.0, "none"


def is_out(p):
    return (p.get("injury_status") or "").upper() in INJURY_OUT


def is_risky(p):
    return (p.get("injury_status") or "").upper() in INJURY_RISK


def pick_lineup(roster, week):
    """最適スタメンを返す。dedicated枠→FLEXの順で埋める(この構成では最適)。"""
    scored = []
    for p in roster:
        s, basis = player_week_score(p, week)
        if is_out(p):
            s = 0.0
        scored.append({**p, "score": round(s, 2), "score_basis": basis})
    scored.sort(key=lambda x: x["score"], reverse=True)

    starters, used = [], set()
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        need = STARTER_SLOTS.get(pos, 0)
        for p in scored:
            if need == 0:
                break
            if p["name"] in used or p["position"] != pos:
                continue
            starters.append({**p, "slot": pos})
            used.add(p["name"])
            need -= 1

    # FLEX
    for p in scored:
        if p["name"] in used or p["position"] not in FLEX_ELIGIBLE:
            continue
        starters.append({**p, "slot": "FLEX"})
        used.add(p["name"])
        break

    bench = [p for p in scored if p["name"] not in used]

    # 僅差判定: 各スタメンに対し、同枠に入り得るベンチ最良との差
    close_calls = []
    for st in starters:
        eligible = FLEX_ELIGIBLE if st["slot"] == "FLEX" else {st["position"]}
        rivals = [b for b in bench if b["position"] in eligible and not is_out(b)]
        if not rivals:
            continue
        best = max(rivals, key=lambda x: x["score"])
        margin = round(st["score"] - best["score"], 2)
        if margin <= CLOSE_CALL_MARGIN:
            close_calls.append({
                "slot": st["slot"], "starter": st["name"], "starter_score": st["score"],
                "rival": best["name"], "rival_score": best["score"], "margin": margin,
            })
    return starters, bench, close_calls


def fa_recommendations(snapshot, week, top_n=8):
    """ポジション別FA上位と、自ロスターとの比較。"""
    my_team = next(t for t in snapshot["teams"] if t["team_id"] == snapshot["my_team_id"])
    my_by_pos = {}
    for p in my_team["roster"]:
        s, _ = player_week_score(p, week)
        my_by_pos.setdefault(p["position"], []).append((p["name"], round(s, 2)))
    for pos in my_by_pos:
        my_by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    all_mine_scored = sorted(
        [(p["name"], p["position"], round(player_week_score(p, week)[0], 2))
         for p in my_team["roster"]],
        key=lambda x: x[2],
    )
    drop_candidates = [x for x in all_mine_scored if x[1] not in ("D/ST", "K")][:3]

    recs = {}
    for pos, players in (snapshot.get("free_agents") or {}).items():
        rows = []
        for p in players:
            s, basis = player_week_score(p, week)
            if s <= 0:
                continue
            rows.append({**p, "score": round(s, 2), "score_basis": basis})
        rows.sort(key=lambda x: (x["score"], x.get("proj_total", 0)), reverse=True)
        rows = rows[:top_n]

        mine = my_by_pos.get(pos, [])
        my_best = mine[0] if mine else None
        my_worst = mine[-1] if mine else None
        for r in rows:
            r["gain_vs_my_worst"] = round(r["score"] - my_worst[1], 2) if my_worst else None
        recs[pos] = {
            "fa": rows,
            "my_best": my_best,
            "my_worst": my_worst,
        }
    return recs, drop_candidates


def bye_overview(roster):
    byes = {}
    for p in roster:
        b = p.get("bye")
        if b:
            byes.setdefault(int(b), []).append(f'{p["name"]} ({p["position"]})')
    return dict(sorted(byes.items()))


def championship_opponents(roster):
    """Week15-17の対戦相手(トレード・FA判断用の先読み)。"""
    rows = []
    for p in roster:
        opps = p.get("opponents") or {}
        games = p.get("nfl_games") or {}
        wk_info = []
        for w in CHAMPIONSHIP_WEEKS:
            g = games.get(w) or games.get(str(w))
            if g:
                loc = "H" if g.get("is_home") else "A"
                wk_info.append(f'W{w}: {"vs" if loc=="H" else "@"}{g.get("opponent","?")}')
            else:
                o = opps.get(w) or opps.get(str(w))
                wk_info.append(f"W{w}: {o if o else 'BYE?'}")
        rows.append({"name": p["name"], "position": p["position"],
                     "pro_team": p["pro_team"], "weeks": wk_info})
    return rows


def league_table(snapshot):
    teams = sorted(snapshot["teams"], key=lambda t: (t.get("standing") or 99))
    return [{
        "standing": t.get("standing", 0), "name": t["name"], "owner": t["owner"],
        "record": f'{t["wins"]}-{t["losses"]}', "pf": t["points_for"],
        "pa": t["points_against"], "waiver": t.get("waiver_rank", 0),
        "playoff_pct": t.get("playoff_pct", 0),
        "is_me": t["team_id"] == snapshot["my_team_id"],
    } for t in teams]


def build_ai_summary(snapshot, week, starters, bench, close_calls, recs, drop_candidates):
    """無料AIに貼り付ける用の要約テキスト。"""
    my_team = next(t for t in snapshot["teams"] if t["team_id"] == snapshot["my_team_id"])
    L = []
    L.append(f'ファンタジーフットボール(ESPN 6人リーグ/PPR/H2H)の相談。私は「{my_team["name"]}」。Week {week}。')
    L.append("スタメン枠: QB1 RB2 WR2 TE1 FLEX1(RB/WR/TE) DST1 K1、ベンチ6。")
    L.append("")
    L.append("■推奨スタメン(週次予測pt / 自軍Tot=ブックメーカー予想チーム得点 / 天候):")
    for s in starters:
        inj = f' [{s["injury_status"]}]' if s.get("injury_status") and s["injury_status"] != "ACTIVE" else ""
        extra = ""
        if s.get("implied_total") is not None:
            extra += f' Tot{s["implied_total"]}'
        if s.get("weather_str"):
            extra += f' {s["weather_str"]}' + ("(強風注意)" if s.get("wind_warn") else "")
        L.append(f'  {s["slot"]}: {s["name"]} ({s["position"]}/{s["pro_team"]} {s.get("this_week_opp","")}) {s["score"]}pt{inj}{extra}')
    L.append("■ベンチ:")
    for b in bench:
        inj = f' [{b["injury_status"]}]' if b.get("injury_status") and b["injury_status"] != "ACTIVE" else ""
        L.append(f'  {b["name"]} ({b["position"]}/{b["pro_team"]}) {b["score"]}pt{inj}')
    if close_calls:
        L.append("■僅差の判断(相談したいポイント):")
        for c in close_calls:
            L.append(f'  {c["slot"]}: {c["starter"]}({c["starter_score"]}) vs {c["rival"]}({c["rival_score"]}) 差{c["margin"]}pt')
    L.append("")
    L.append("■FA上位(ポジション別、括弧は自分の同ポジ最弱との差):")
    for pos, r in recs.items():
        if not r["fa"]:
            continue
        tops = ", ".join(
            f'{x["name"]}{x["score"]}pt' + (f'(+{x["gain_vs_my_worst"]})' if x.get("gain_vs_my_worst") and x["gain_vs_my_worst"] > 0 else "")
            for x in r["fa"][:3])
        L.append(f'  {pos}: {tops}')
    if drop_candidates:
        L.append("■ドロップ候補(自ロスター低予測順): " + ", ".join(f'{n}({p}){s}pt' for n, p, s in drop_candidates))
    L.append("")
    L.append("質問: このスタメンとFA獲得判断で見落としはある?僅差の枠はどちらを使うべき?理由も教えて。")
    return "\n".join(L)
