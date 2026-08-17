# -*- coding: utf-8 -*-
"""スナップショットからスタメン推奨・FA推奨・Bye週マップ等を計算する。"""
from ff_config import (
    STARTER_SLOTS, FLEX_ELIGIBLE, INJURY_OUT, INJURY_RISK,
    CLOSE_CALL_MARGIN, CHAMPIONSHIP_WEEKS,
    ROOKIE_RULE_UNTIL_WEEK, ROOKIE_MIN_COUNT,
)


def player_value_ppg(p):
    """選手の見込みpt/G(v3.1)。ブレンド値(ESPN予測×実力推定)があれば優先。

    ヒートマップ・モンテカルロ・トレード評価が共通で使う「番付」の物差し。
    開幕前・機会データなしの選手はESPN季節予測にフォールバック。
    """
    v = p.get("blend_ppg")
    if v is not None:
        return float(v)
    if p.get("proj_avg"):
        return float(p["proj_avg"])
    if p.get("proj_total"):
        return float(p["proj_total"]) / 14.0
    return 0.0


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

    my_rookie_count = sum(1 for p in my_team["roster"] if p.get("is_rookie"))
    protect_rookies = week <= ROOKIE_RULE_UNTIL_WEEK and my_rookie_count <= ROOKIE_MIN_COUNT
    all_mine_scored = sorted(
        [(p["name"], p["position"], round(player_week_score(p, week)[0], 2), bool(p.get("is_rookie")))
         for p in my_team["roster"]],
        key=lambda x: x[2],
    )
    zone_by_name = {p["name"]: p.get("drop_zone") for p in my_team["roster"]}
    zone_icon = {"safe": "✅", "hold": "🚫", "watch": "🟡", "core": "🚫"}
    drop_candidates = [
        (n, pos + (" R" if rk else ""), s, zone_icon.get(zone_by_name.get(n), ""))
        for n, pos, s, rk in all_mine_scored
        if pos not in ("D/ST", "K") and not (rk and protect_rookies)
        and zone_by_name.get(n) not in ("hold", "core")
    ][:3]

    recs = {}
    for pos, players in (snapshot.get("free_agents") or {}).items():
        rows = []
        for p in players:
            s, basis = player_week_score(p, week)
            if s <= 0:
                continue
            # 来週の見込み(Bye週はNone→レポート側でBYE表示)。先取り判断用
            if p.get("next_week_opp") == "BYE":
                next_score = None
            else:
                ns, _ = player_week_score(p, week + 1)
                next_score = round(ns, 1)
            rows.append({**p, "score": round(s, 2), "score_basis": basis,
                         "next_score": next_score})
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


def rookie_swap(snapshot, week, top_n=10):
    """Week5ルール用: 自分のルーキーと、FAで獲れるルーキーの比較。

    ルール期間(week <= ROOKIE_RULE_UNTIL_WEEK)のみ返す。それ以外はNone。
    """
    if week > ROOKIE_RULE_UNTIL_WEEK:
        return None
    my_team = next(t for t in snapshot["teams"] if t["team_id"] == snapshot["my_team_id"])
    mine = []
    for p in my_team["roster"]:
        if p.get("is_rookie"):
            s, _ = player_week_score(p, week)
            mine.append({**p, "score": round(s, 2)})
    mine.sort(key=lambda x: x["score"], reverse=True)
    my_worst = mine[-1]["score"] if mine else None

    fa_rookies = []
    for pos, players in (snapshot.get("free_agents") or {}).items():
        for p in players:
            if p.get("is_rookie"):
                s, _ = player_week_score(p, week)
                if s <= 0:
                    continue
                gain = round(s - my_worst, 2) if my_worst is not None else None
                fa_rookies.append({**p, "score": round(s, 2), "gain_vs_my_worst_rookie": gain})
    fa_rookies.sort(key=lambda x: x["score"], reverse=True)

    return {
        "my_rookies": mine,
        "fa_rookies": fa_rookies[:top_n],
        "my_count": len(mine),
        "min_count": ROOKIE_MIN_COUNT,
        "until_week": ROOKIE_RULE_UNTIL_WEEK,
    }


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


def build_ai_summary(snapshot, week, starters, bench, close_calls, recs, drop_candidates,
                     rookie_info=None, opp_info=None):
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
        if s["position"] == "D/ST":
            if s.get("opp_implied") is not None:
                extra += f' 相手Tot{s["opp_implied"]}(低いほど良い)'
            m = s.get("dst_opp_metrics")
            if m:
                extra += f' 相手被Sk{m["sk_g"]}/G・TO{m["to_g"]}/G({m["season"]}実績)'
        elif s.get("implied_total") is not None:
            extra += f' Tot{s["implied_total"]}'
        if s.get("is_rookie"):
            extra += " [ルーキー]"
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
        L.append("■ドロップ候補(自ロスター低予測順、\"R\"=ルーキー、✅=期待も機会も低く安全/🟡=期待は高いが機会低下・1週様子見推奨): "
                 + ", ".join(f'{z}{n}({p}){s}pt' for n, p, s, z in drop_candidates))
    if opp_info and opp_info.get("available"):
        mode_s = "アーリーモード(単週判定・確度低め)" if opp_info["mode"] == "early" else "通常モード"
        L.append(f'■機会指標アラート(nflverse実データ W{opp_info["last_week"]}時点、{mode_s}):')
        L.append("  タグ: 🔥=機会がスタメン級なのに得点まだ(ブレイク前夜・即獲得検討) 👀=その一歩手前 "
                 "💎=既に得点も機会も実証済みなのにFAに放置 ⚠️=自軍で機会が減少中(売り時)")
        for p in opp_info["fa_tagged"][:8]:
            o = p.get("opp") or {}
            det = []
            if o.get("snap") is not None:
                det.append(f'Snap{round(o["snap"]*100)}%')
            if o.get("ts") is not None and p["position"] != "QB":
                det.append(f'TS{round(o["ts"]*100)}%')
            if o.get("ppg3") is not None:
                det.append(f'直近{o["ppg3"]}pt/G')
            L.append(f'  {p["opp_tag"]} {p["name"]} ({p["position"]}/{p["pro_team"]}) ' + " ".join(det))
        for p in opp_info["my_sell"]:
            L.append(f'  ⚠️ {p["name"]} (自軍{p["position"]}): {p.get("sell_reason","機会低下")}')
        for sp in opp_info["swap_pairs"][:5]:
            L.append(f'  入替案: {sp["add"]["opp_tag"]}{sp["add"]["name"]}を獲得 ⇄ '
                     f'{sp["drop"]["name"]}を放出 ({sp["drop_zone_note"]}) ネット{sp["net"]:+}pt/G')
    if rookie_info:
        L.append(f'■リーグルール: Week{rookie_info["until_week"]}終了までNFL1年目の選手を常に{rookie_info["min_count"]}人以上ロスターに保持する義務あり。')
        L.append(f'  現在の保有ルーキー({rookie_info["my_count"]}人): ' + ", ".join(
            f'{p["name"]}({p["position"]}){p["score"]}pt' for p in rookie_info["my_rookies"]) )
        if rookie_info["fa_rookies"]:
            L.append("  FAで獲れるルーキー上位(括弧=自分の最弱ルーキーとの差): " + ", ".join(
                f'{p["name"]}({p["position"]}){p["score"]}pt'
                + (f'(+{p["gain_vs_my_worst_rookie"]})' if p.get("gain_vs_my_worst_rookie") and p["gain_vs_my_worst_rookie"] > 0 else "")
                for p in rookie_info["fa_rookies"][:5]))
        L.append("  ルーキーを切る場合は必ず別のルーキーの獲得とセットで(同時に行う)こと。")
    L.append("")
    L.append("質問: このスタメンとFA獲得判断で見落としはある?僅差の枠はどちらを使うべき?理由も教えて。")
    return "\n".join(L)
