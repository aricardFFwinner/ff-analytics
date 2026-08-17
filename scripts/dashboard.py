# -*- coding: utf-8 -*-
"""P2: リーグダッシュボードの計算部。

- 6チーム×ポジションの戦力表(pt/G、リーグ平均比)
- 余剰/不足の文章化
- W15-17のポジション別SoS(守備が各ポジションに許したFP。
  当季データ3週未満は前季実績にフォールバック)
- history.json(週キーで上書き蓄積)と結論バナー素材
"""
import csv
import io
import json
import os

from ff_config import (
    SEASON, STARTER_SLOTS, FLEX_ELIGIBLE, STARTABLE_RANK, CHAMPIONSHIP_WEEKS,
)

OFFENSE_POS = ("QB", "RB", "WR", "TE")
DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
HISTORY_PATH = os.path.join(DOCS, "data", "history.json")


def _num(v):
    try:
        return float(v) if v not in (None, "", "NA") else 0.0
    except (ValueError, TypeError):
        return 0.0


# ------------------------------------------------------------------
# 戦力表(ポジション別 pt/G)
# ------------------------------------------------------------------

def team_strength_table(snapshot):
    """各チームのポジション別戦力(スタメン枠のproj_avg合計 pt/G)を返す。

    返り値: {"rows": [{team_id,name,owner,by_pos:{pos:pt},total}], "avg": {pos:pt}}
    FLEXは各ポジション枠を埋めた後の最良のRB/WR/TE。
    """
    import analysis
    rows = []
    for t in snapshot["teams"]:
        pool = sorted(t["roster"], key=analysis.player_value_ppg, reverse=True)
        used = set()
        by_pos = {}
        for pos in ("QB", "RB", "WR", "TE", "D/ST", "K"):
            need = STARTER_SLOTS.get(pos, 0)
            got, val = 0, 0.0
            for p in pool:
                if got >= need:
                    break
                if id(p) in used or p.get("position") != pos:
                    continue
                val += analysis.player_value_ppg(p)
                used.add(id(p))
                got += 1
            by_pos[pos] = round(val, 1)
        flex = 0.0
        for p in pool:
            if id(p) not in used and p.get("position") in FLEX_ELIGIBLE:
                flex = analysis.player_value_ppg(p)
                break
        by_pos["FLEX"] = round(flex, 1)
        total = round(sum(by_pos.values()), 1)
        rows.append({"team_id": t["team_id"], "name": t["name"],
                     "owner": t.get("owner", ""), "by_pos": by_pos, "total": total})
    positions = ["QB", "RB", "WR", "TE", "FLEX", "D/ST", "K"]
    avg = {pos: round(sum(r["by_pos"][pos] for r in rows) / len(rows), 1)
           for pos in positions}
    avg["total"] = round(sum(r["total"] for r in rows) / len(rows), 1)
    return {"rows": rows, "avg": avg, "positions": positions}


# ------------------------------------------------------------------
# 余剰 / 不足
# ------------------------------------------------------------------

def _expectation_lines(snapshot):
    """現在の戦力(ブレンド値)ベースのスタメン級ライン(v3.1で事前予測→現在値に変更)。"""
    import analysis
    pool = {}
    for t in snapshot["teams"]:
        for p in t["roster"]:
            pool.setdefault(p["position"], []).append(analysis.player_value_ppg(p))
    for pos, players in (snapshot.get("free_agents") or {}).items():
        for p in players:
            pool.setdefault(pos, []).append(analysis.player_value_ppg(p))
    lines = {}
    for pos, rank in STARTABLE_RANK.items():
        vals = sorted(pool.get(pos, []), reverse=True)
        lines[pos] = vals[rank - 1] if len(vals) >= rank else (vals[-1] if vals else 0.0)
    return lines


def surplus_deficit(snapshot):
    """各チームの余剰(スタメン級がスタメン枠を超えて何人いるか)と不足を文章化。"""
    import analysis
    val = analysis.player_value_ppg
    lines = _expectation_lines(snapshot)
    out = []
    for t in snapshot["teams"]:
        surplus, deficit = [], []
        for pos in OFFENSE_POS:
            grade = sum(1 for p in t["roster"]
                        if p["position"] == pos and val(p) >= lines[pos])
            extra = grade - STARTER_SLOTS.get(pos, 0)
            if extra >= 2 or (extra >= 1 and pos in ("QB", "TE")):
                surplus.append(f"{pos}+{extra}")
            starters = sorted((p for p in t["roster"] if p["position"] == pos),
                              key=val, reverse=True)
            starters = starters[:STARTER_SLOTS.get(pos, 0)]
            weak = sum(1 for p in starters if val(p) < lines[pos])
            if len(starters) < STARTER_SLOTS.get(pos, 0) or weak:
                deficit.append(pos)
        out.append({"team_id": t["team_id"], "name": t["name"], "owner": t.get("owner", ""),
                    "surplus": surplus, "deficit": deficit})
    return {"rows": out, "lines": {k: round(v, 1) for k, v in lines.items()}}


# ------------------------------------------------------------------
# W15-17 SoS(守備が各ポジションに許したFP)
# ------------------------------------------------------------------

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"


def _download_text(url):
    import urllib.request
    cache_dir = os.environ.get("OPP_CACHE_DIR")
    if cache_dir:
        path = os.path.join(cache_dir, url.rsplit("/", 1)[-1])
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return data.decode("utf-8", errors="replace")


def fetch_def_vs_pos():
    """守備チーム別・ポジション別の被FP/G。当季3週未満なら前季を使う。

    返り値: {"season": 使用季, "table": {(def_team,pos): 被FP/G},
             "rank": {(def_team,pos): 1..32 (1=最も許す=おいしい相手)}}
    """
    for season_try in (SEASON, SEASON - 1):
        try:
            text = _download_text(STATS_URL.format(season=season_try))
        except Exception as e:
            print(f"[info] {season_try}被FPデータ取得スキップ: {e}")
            continue
        agg = {}  # (def,pos) -> {week: pts}
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("season_type") != "REG":
                continue
            pos = row.get("position")
            if pos not in OFFENSE_POS:
                continue
            d = row.get("opponent_team")
            if not d:
                continue
            wk = int(row["week"])
            key = (d, pos)
            agg.setdefault(key, {}).setdefault(wk, 0.0)
            agg[key][wk] += _num(row.get("fantasy_points_ppr"))
        if not agg:
            continue
        games_max = max(len(w) for w in agg.values())
        if season_try == SEASON and games_max < 3:
            print(f"[info] {season_try}被FPは{games_max}週分のみ → 前季にフォールバック")
            continue
        table = {k: round(sum(w.values()) / len(w), 1) for k, w in agg.items()}
        rank = {}
        for pos in OFFENSE_POS:
            defs = sorted((k for k in table if k[1] == pos),
                          key=lambda k: -table[k])
            for i, k in enumerate(defs):
                rank[k] = i + 1
        print(f"[info] 被FP(SoS用): {season_try}シーズン実績を使用")
        return {"season": season_try, "table": table, "rank": rank}
    return None


def champ_sos(snapshot, defvs):
    """チーム×ポジションのW15-17マッチアップ難易度。

    スコア = スタメン級選手が対戦する守備の「被FPランク」の平均(1-32、小さいほど楽)。
    ランク→評価: 1-10=🟢楽 / 11-22=中 / 23-32=🔴きつい
    """
    if not defvs:
        return None
    import analysis
    import nfl_schedule
    out = []
    for t in snapshot["teams"]:
        by_pos = {}
        for pos in OFFENSE_POS:
            starters = sorted((p for p in t["roster"] if p["position"] == pos),
                              key=analysis.player_value_ppg, reverse=True)
            starters = starters[:max(STARTER_SLOTS.get(pos, 0), 1)]
            ranks = []
            for p in starters:
                games = p.get("nfl_games") or {}
                for w in CHAMPIONSHIP_WEEKS:
                    g = games.get(w) or games.get(str(w))
                    if not g:
                        continue
                    opp = nfl_schedule.to_nflverse_abbrev(g.get("opponent", ""))
                    r = defvs["rank"].get((opp, pos))
                    if r:
                        ranks.append(r)
            if ranks:
                by_pos[pos] = round(sum(ranks) / len(ranks), 1)
        out.append({"team_id": t["team_id"], "name": t["name"],
                    "owner": t.get("owner", ""), "by_pos": by_pos})
    return {"rows": out, "season": defvs["season"]}


# ------------------------------------------------------------------
# history.json(週キーで上書き)
# ------------------------------------------------------------------

def load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(history, week, sim_result, strength, generated_at):
    entry = {"generated_at": generated_at, "teams": {}}
    for tid, m in (sim_result or {}).get("teams", {}).items():
        entry["teams"][str(tid)] = {
            "champ_pct": m["champ_pct"], "playoff_pct": m["playoff_pct"],
        }
    for r in strength["rows"]:
        entry["teams"].setdefault(str(r["team_id"]), {})["power"] = r["total"]
    history[str(week)] = entry
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    return history


def prev_week_champ_pct(history, week, team_id):
    for w in range(week - 1, 0, -1):
        e = history.get(str(w))
        if e and str(team_id) in e.get("teams", {}):
            v = e["teams"][str(team_id)].get("champ_pct")
            if v is not None:
                return v, w
    return None, None


# ------------------------------------------------------------------
# eval_log: 予測方式の検証ログ(W5/W10/シーズン後のチェックポイントで採点)
# ------------------------------------------------------------------

EVAL_LOG_PATH = os.path.join(DOCS, "data", "eval_log.json")


def write_eval_log(snapshot, week, generated_at):
    """今週の各チーム得点予測をESPNのみ版/ブレンド版の両方で記録(週キー上書き)。

    実スコアはESPNスナップショットのweekly_scoresに残るため、後からMAE採点できる。
    """
    import simulate
    try:
        with open(EVAL_LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        log = {}
    entry = {"generated_at": generated_at, "preds": {}}
    for t in snapshot["teams"]:
        entry["preds"][str(t["team_id"])] = {
            "espn": round(simulate._lineup_total(t["roster"], week, use_blend=False), 1),
            "blend": round(simulate._lineup_total(t["roster"], week, use_blend=True), 1),
        }
    log[str(week)] = entry
    os.makedirs(os.path.dirname(EVAL_LOG_PATH), exist_ok=True)
    with open(EVAL_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def score_eval_log(snapshot):
    """チェックポイント用: eval_logと実スコアを突き合わせてMAEを返す(手動実行)。"""
    try:
        with open(EVAL_LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return None
    actual = {}
    for t in snapshot["teams"]:
        for wk, sc in (t.get("weekly_scores") or {}).items():
            actual[(str(t["team_id"]), str(wk))] = float(sc)
    errs = {"espn": [], "blend": []}
    detail = []
    for wk, e in sorted(log.items(), key=lambda kv: int(kv[0])):
        for tid, pred in e.get("preds", {}).items():
            a = actual.get((tid, wk))
            if a is None or a <= 0:
                continue
            errs["espn"].append(abs(pred["espn"] - a))
            errs["blend"].append(abs(pred["blend"] - a))
            detail.append({"week": int(wk), "team_id": int(tid), "actual": a,
                           "espn": pred["espn"], "blend": pred["blend"]})
    if not errs["espn"]:
        return None
    return {"n": len(errs["espn"]),
            "mae_espn": round(sum(errs["espn"]) / len(errs["espn"]), 2),
            "mae_blend": round(sum(errs["blend"]) / len(errs["blend"]), 2),
            "detail": detail}


# ------------------------------------------------------------------
# 生成の入口(weekly_report.pyから呼ぶ)
# ------------------------------------------------------------------

def build_and_write(snapshot, week, generated_at):
    """ダッシュボードを計算し docs/dashboard.html と週別アーカイブを書き出す。

    失敗しても週次レポートを止めないよう、呼び出し側でtry/exceptすること。
    返り値: ログ用サマリー文字列。
    """
    import simulate
    import dashboard_html

    sim = simulate.run(snapshot, week)
    strength = team_strength_table(snapshot)
    surplus = surplus_deficit(snapshot)
    try:
        write_eval_log(snapshot, week, generated_at)
    except Exception as e:
        print(f"[warn] eval_log書き込み失敗(続行): {e}")
    try:
        defvs = fetch_def_vs_pos()
    except Exception as e:
        print(f"[warn] 被FP取得失敗(SoSなしで続行): {e}")
        defvs = None
    sos = champ_sos(snapshot, defvs) if defvs else None

    history = load_history()
    my_id = snapshot["my_team_id"]
    prev_champ, _ = prev_week_champ_pct(history, week, my_id)
    if sim:
        history = save_history(history, week, sim, strength, generated_at)

    # P2完成版: トレード候補 + 競合カード(失敗しても骨格部分は出す)
    trade_list, cards = [], []
    try:
        import trades
        trade_list = trades.generate_trades(snapshot, week, surplus, sos)
        cards = trades.competitor_cards(snapshot, week, sim, surplus, sos, history)
    except Exception as e:
        print(f"[warn] トレード/競合カード生成失敗(骨格のみで続行): {e}")
        import traceback
        traceback.print_exc()

    base_ctx = {
        "league_name": snapshot["league_name"],
        "teams": snapshot["teams"],
        "my_team_id": my_id,
        "week": week,
        "generated_at": generated_at,
        "sim": sim,
        "strength": strength,
        "surplus": surplus,
        "sos": sos,
        "history": history,
        "prev_champ": prev_champ,
        "trades": trade_list,
        "cards": cards,
    }
    weeks_avail = sorted(int(w) for w in history.keys())

    # 最新版(docs/dashboard.html)
    ctx = dict(base_ctx)
    ctx["report_href"] = "index.html"
    ctx["week_links"] = [(w, f"dashboard/w{w:02d}.html") for w in weeks_avail if w != week]
    html_latest = dashboard_html.render(ctx)
    with open(os.path.join(DOCS, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html_latest)

    # 週別アーカイブ(docs/dashboard/wNN.html)
    ctx = dict(base_ctx)
    ctx["report_href"] = "../index.html"
    ctx["week_links"] = [(w, f"w{w:02d}.html") for w in weeks_avail if w != week]
    html_arch = dashboard_html.render(ctx)
    arch_dir = os.path.join(DOCS, "dashboard")
    os.makedirs(arch_dir, exist_ok=True)
    with open(os.path.join(arch_dir, f"w{week:02d}.html"), "w", encoding="utf-8") as f:
        f.write(html_arch)

    if sim:
        me = sim["teams"][my_id]
        return (f"ダッシュボード生成: W{week} 優勝{me['champ_pct']}% PO{me['playoff_pct']}%"
                f" (シミュ{sim['sims']}回)")
    return f"ダッシュボード生成: W{week} (対戦日程なし→シミュレーションはスキップ)"
