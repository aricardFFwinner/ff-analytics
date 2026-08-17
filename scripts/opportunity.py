# -*- coding: utf-8 -*-
"""P1.5: nflverse機会指標(Opportunity Metrics)。

選手×週の ターゲットシェア / スナップ率 / RZタッチ / エアヤードシェア / WOPR /
加重機会 を構築し、FA向け 🔥👀(ブレイク前夜)・💎(放置バリュー)、
自軍向け ⚠️(売り時)・ドロップ2軸マトリクス・入れ替えペアを生成する。

データは全てnflverse系のGitHubリリース(無認証・無料):
  - stats_player_week_{season}.csv : 週次スタッツ(targets/air yards/carries/PPR)
  - snap_counts_{season}.csv       : スナップ率
  - play_by_play_{season}.csv.gz   : レッドゾーンタッチ/ターゲット
  - ep_weekly_{season}.csv         : xFP(期待FP、ffopportunity。参考列)
  - games.csv                      : 試合スコア(ブローアウト週の判定)

開幕前などデータ未公開(404)の場合は None を返し、レポート側は
機会指標なしで従来どおり動作する。閾値は ff_config.py に集約。
"""
import csv
import gzip
import io
import os
import urllib.request

from ff_config import (
    SEASON, ESPN_TO_NFLVERSE, STARTER_SLOTS, FLEX_ELIGIBLE,
    EARLY_MODE_UNTIL_WEEK, OPP_RECENT_WEEKS, PPG_RECENT_WEEKS,
    STARTABLE_RANK, BLOWOUT_MARGIN,
    FIRE_WRTE, WATCH_WRTE, FIRE_RB, WATCH_RB,
    FIRE_WRTE_E, WATCH_WRTE_E, FIRE_RB_E, WATCH_RB_E,
    RZ_PROMOTE_COUNT, SELL_DECLINE_RATIO, DIAMOND_BACKING, DIAMOND_MIN_GAMES,
    FIRE_WATCH_POSITIONS,
)
from nfl_extra import norm_name

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
SNAPS_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
XFP_URL = "https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.csv"
GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"

OFFENSE_POS = {"QB", "RB", "WR", "TE"}


def _num(v):
    try:
        return float(v) if v not in (None, "", "NA") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _download(url, timeout=120):
    """取得。OPP_CACHE_DIR設定時はローカルキャッシュを使う(検証用)。"""
    cache_dir = os.environ.get("OPP_CACHE_DIR")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, url.rsplit("/", 1)[-1])
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    if cache_dir:
        with open(path, "wb") as f:
            f.write(data)
    return data


def _try_download_text(url):
    try:
        return _download(url).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[info] 取得スキップ {url.rsplit('/',1)[-1]}: {e}")
        return None


# ------------------------------------------------------------------
# データ構築
# ------------------------------------------------------------------

def fetch_opportunity_data(season=SEASON, upto_week=None):
    """機会指標テーブルを構築して返す。データ未公開ならNone。

    upto_week: 検証用。指定するとその週までのデータだけで構築(as-of再現)。
    """
    stats_text = _try_download_text(STATS_URL.format(season=season))
    if not stats_text or "player_id" not in stats_text[:200]:
        print(f"[info] {season}週次スタッツ未公開 → 機会指標なしで続行")
        return None

    players = {}   # gsis_id -> {"name","team","position","weeks":{wk:{...}}}
    team_week = {} # (team, wk) -> {"targets": x, "air": y}

    for row in csv.DictReader(io.StringIO(stats_text)):
        if row.get("season_type") != "REG":
            continue
        wk = int(row["week"])
        if upto_week and wk > upto_week:
            continue
        team = row.get("team") or ""
        tw = team_week.setdefault((team, wk), {"targets": 0.0, "air": 0.0})
        tgt = _num(row.get("targets"))
        air = _num(row.get("receiving_air_yards"))
        tw["targets"] += tgt
        tw["air"] += max(air, 0.0)

        pos = row.get("position") or ""
        if pos not in OFFENSE_POS:
            continue
        pid = row["player_id"]
        rec = players.setdefault(pid, {
            "name": row.get("player_display_name") or row.get("player_name") or "?",
            "team": team, "position": pos, "weeks": {},
        })
        rec["team"] = team  # 最新週の所属を保持
        carries = _num(row.get("carries"))
        rec["weeks"][wk] = {
            "team": team,  # 移籍対応: シェア計算はその週の所属チームで行う
            "targets": tgt, "air": max(air, 0.0), "carries": carries,
            "wtd": carries + 1.5 * tgt,
            "ppr": _num(row.get("fantasy_points_ppr")),
            "snap": None, "rz": 0, "xfp": None,
        }

    if not players:
        print("[info] 週次スタッツにデータ行なし → 機会指標なしで続行")
        return None

    last_week = max(wk for p in players.values() for wk in p["weeks"])

    # シェア系を計算(その週の所属チームの合計に対する割合)
    for p in players.values():
        for wk, w in p["weeks"].items():
            tw = team_week.get((w["team"], wk)) or {}
            tt, ta = tw.get("targets", 0.0), tw.get("air", 0.0)
            w["ts"] = round(w["targets"] / tt, 3) if tt > 0 else 0.0
            w["as"] = round(w["air"] / ta, 3) if ta > 0 else 0.0
            w["wopr"] = round(1.5 * w["ts"] + 0.7 * w["as"], 3)

    # スナップ率
    snaps_text = _try_download_text(SNAPS_URL.format(season=season))
    if snaps_text:
        by_name_team = {}
        for pid, p in players.items():
            by_name_team[(norm_name(p["name"]), p["team"])] = pid
            by_name_team.setdefault((norm_name(p["name"]), None), pid)
        for row in csv.DictReader(io.StringIO(snaps_text)):
            if row.get("game_type") != "REG":
                continue
            wk = int(row["week"])
            if upto_week and wk > upto_week:
                continue
            key = (norm_name(row.get("player", "")), row.get("team"))
            pid = by_name_team.get(key) or by_name_team.get((key[0], None))
            if not pid:
                continue
            wrec = players[pid]["weeks"].get(wk)
            if wrec is not None:
                wrec["snap"] = _num(row.get("offense_pct"))

    # レッドゾーン(タッチ+ターゲット)
    try:
        raw = _download(PBP_URL.format(season=season))
        need = ["week", "season_type", "yardline_100", "pass_attempt", "rush_attempt",
                "rusher_player_id", "receiver_player_id"]
        with gzip.open(io.BytesIO(raw), "rt", errors="replace") as f:
            r = csv.reader(f)
            header = next(r)
            idx = {c: header.index(c) for c in need}
            for row in r:
                if row[idx["season_type"]] != "REG":
                    continue
                try:
                    wk = int(row[idx["week"]])
                    yl = float(row[idx["yardline_100"]])
                except ValueError:
                    continue
                if (upto_week and wk > upto_week) or yl > 20:
                    continue
                for col in ("rusher_player_id", "receiver_player_id"):
                    pid = row[idx[col]]
                    if pid and pid in players:
                        wrec = players[pid]["weeks"].get(wk)
                        if wrec is not None:
                            wrec["rz"] += 1
    except Exception as e:
        print(f"[warn] PBP(レッドゾーン)取得失敗(RZなしで続行): {e}")

    # xFP(参考列。PPR換算を成分から自前計算してスコアリングの曖昧さを排除)
    xfp_text = _try_download_text(XFP_URL.format(season=season))
    if xfp_text:
        for row in csv.DictReader(io.StringIO(xfp_text)):
            pid = row.get("player_id")
            if pid not in players:
                continue
            try:
                wk = int(row["week"])
            except (ValueError, TypeError):
                continue
            if upto_week and wk > upto_week:
                continue
            wrec = players[pid]["weeks"].get(wk)
            if wrec is None:
                continue
            xfp = (_num(row.get("receptions_exp"))
                   + 0.1 * _num(row.get("rec_yards_gained_exp"))
                   + 6.0 * _num(row.get("rec_touchdown_exp"))
                   + 0.1 * _num(row.get("rush_yards_gained_exp"))
                   + 6.0 * _num(row.get("rush_touchdown_exp"))
                   + 0.04 * _num(row.get("pass_yards_gained_exp"))
                   + 4.0 * _num(row.get("pass_touchdown_exp"))
                   - 2.0 * _num(row.get("pass_interception_exp")))
            wrec["xfp"] = round(xfp, 1)

    # ブローアウト週 (チーム, 週) 集合
    blowouts = set()
    games_text = _try_download_text(GAMES_URL)
    if games_text:
        for row in csv.DictReader(io.StringIO(games_text)):
            if row.get("season") != str(season) or row.get("game_type") != "REG":
                continue
            hs, as_ = row.get("home_score"), row.get("away_score")
            if hs in (None, "", "NA") or as_ in (None, "", "NA"):
                continue
            if abs(_num(hs) - _num(as_)) >= BLOWOUT_MARGIN:
                wk = int(row["week"])
                blowouts.add((row["home_team"], wk))
                blowouts.add((row["away_team"], wk))

    # スタメン級ライン(直近PPG基準、ポジション別)
    startable = {}
    for pos, rank in STARTABLE_RANK.items():
        ppgs = []
        for p in players.values():
            if p["position"] != pos:
                continue
            ppg = _recent_avg(p["weeks"], "ppr", PPG_RECENT_WEEKS, last_week)
            if ppg is not None:
                ppgs.append(ppg)
        ppgs.sort(reverse=True)
        startable[pos] = round(ppgs[rank - 1], 1) if len(ppgs) >= rank else (
            round(ppgs[-1], 1) if ppgs else 0.0)

    # 名前インデックス(ESPN照合用)
    name_index = {}
    for pid, p in players.items():
        name_index.setdefault(norm_name(p["name"]), []).append(pid)

    mode = "early" if last_week <= EARLY_MODE_UNTIL_WEEK else "normal"
    print(f"[info] 機会指標: {season} W{last_week}まで {len(players)}名 "
          f"(モード: {'アーリー(単週判定)' if mode == 'early' else '通常'})")
    return {
        "season": season, "last_week": last_week, "mode": mode,
        "players": players, "name_index": name_index,
        "startable": startable, "blowouts": blowouts,
    }


# ------------------------------------------------------------------
# 集計ヘルパー
# ------------------------------------------------------------------

def _played_weeks(weeks, last_week):
    return sorted(w for w in weeks if w <= last_week)


def _recent_avg(weeks, key, n, last_week):
    """直近n出場週の平均。出場週ゼロならNone。snapはNone値を除外。"""
    ws = _played_weeks(weeks, last_week)[-n:]
    vals = [weeks[w][key] for w in ws if weeks[w].get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _latest(weeks, key, last_week):
    ws = _played_weeks(weeks, last_week)
    for w in reversed(ws):
        v = weeks[w].get(key)
        if v is not None:
            return v
    return None


def _trend_arrow(weeks, key, last_week):
    ws = _played_weeks(weeks, last_week)
    vals = [weeks[w].get(key) for w in ws if weeks[w].get(key) is not None]
    if len(vals) < 2:
        return "→"
    if len(vals) >= 3 and vals[-1] > vals[-2] > vals[-3]:
        return "↗↗"
    if vals[-1] > vals[-2]:
        return "↗"
    if len(vals) >= 3 and vals[-1] < vals[-2] < vals[-3]:
        return "↘↘"
    if vals[-1] < vals[-2]:
        return "↘"
    return "→"


def _rising(weeks, key, last_week):
    return "↗" in _trend_arrow(weeks, key, last_week)


def _main_metric(pos):
    """トレンド・売り時判定に使う主指標。"""
    return "wtd" if pos == "RB" else "wopr"


# ------------------------------------------------------------------
# ESPN選手との照合
# ------------------------------------------------------------------

def match_player(opp, espn_player):
    """ESPN選手dictに対応するnflverse選手recordを返す(なければNone)。"""
    if not opp or espn_player.get("position") not in OFFENSE_POS:
        return None
    pids = opp["name_index"].get(norm_name(espn_player.get("name", "")))
    if not pids:
        return None
    if len(pids) == 1:
        return opp["players"][pids[0]]
    team = ESPN_TO_NFLVERSE.get(espn_player.get("pro_team", ""), espn_player.get("pro_team", ""))
    for pid in pids:
        if opp["players"][pid]["team"] == team:
            return opp["players"][pid]
    for pid in pids:
        if opp["players"][pid]["position"] == espn_player["position"]:
            return opp["players"][pid]
    return opp["players"][pids[0]]


def metrics_summary(opp, rec):
    """レポート表示用の要約メトリクスを返す。"""
    lw = opp["last_week"]
    wk = rec["weeks"]
    return {
        "snap": _latest(wk, "snap", lw),
        "snap_avg": _recent_avg(wk, "snap", OPP_RECENT_WEEKS, lw),
        "ts": _latest(wk, "ts", lw),
        "ts_avg": _recent_avg(wk, "ts", OPP_RECENT_WEEKS, lw),
        "wopr_avg": _recent_avg(wk, "wopr", OPP_RECENT_WEEKS, lw),
        "wtd_avg": _recent_avg(wk, "wtd", OPP_RECENT_WEEKS, lw),
        "rz2": sum(wk[w]["rz"] for w in _played_weeks(wk, lw)[-2:]),
        "ppg3": _recent_avg(wk, "ppr", PPG_RECENT_WEEKS, lw),
        "xfp3": _recent_avg(wk, "xfp", PPG_RECENT_WEEKS, lw),
        "trend": _trend_arrow(wk, _main_metric(rec["position"]), lw),
        "games": len(_played_weeks(wk, lw)),
    }


# ------------------------------------------------------------------
# タグ判定
# ------------------------------------------------------------------

def _opportunity_level(rec, opp, early):
    """機会の水準: 2(🔥級) / 1(👀級) / 0。earlyは単週値、通常は直近平均。"""
    lw = opp["last_week"]
    wk = rec["weeks"]
    pos = rec["position"]
    if early:
        snap = _latest(wk, "snap", lw)
        if pos == "RB":
            wtd = _latest(wk, "wtd", lw) or 0
            if wtd >= FIRE_RB_E["wtd"] and (snap or 0) >= FIRE_RB_E["snap"]:
                return 2
            if wtd >= WATCH_RB_E["wtd"] and (snap or 0) >= WATCH_RB_E["snap"]:
                return 1
        elif pos in ("WR", "TE"):
            ts = _latest(wk, "ts", lw) or 0
            wopr = _latest(wk, "wopr", lw) or 0
            if (ts >= FIRE_WRTE_E["ts"] or wopr >= FIRE_WRTE_E["wopr"]) and \
               (snap or 0) >= FIRE_WRTE_E["snap"]:
                return 2
            if (ts >= WATCH_WRTE_E["ts"] or wopr >= WATCH_WRTE_E["wopr"]) and \
               (snap or 0) >= WATCH_WRTE_E["snap"]:
                return 1
        elif pos == "QB":
            # QBはスナップ率のみで判定(先発の座を持っているか)
            if (snap or 0) >= DIAMOND_BACKING["QB"]["snap"]:
                return 1
        return 0
    if pos == "RB":
        wtd = _recent_avg(wk, "wtd", OPP_RECENT_WEEKS, lw) or 0
        snap = _recent_avg(wk, "snap", OPP_RECENT_WEEKS, lw) or 0
        if wtd >= FIRE_RB["wtd"] and snap >= FIRE_RB["snap"]:
            return 2
        if wtd >= WATCH_RB["wtd"] and snap >= WATCH_RB["snap"]:
            return 1
    elif pos in ("WR", "TE"):
        ts = _recent_avg(wk, "ts", OPP_RECENT_WEEKS, lw) or 0
        wopr = _recent_avg(wk, "wopr", OPP_RECENT_WEEKS, lw) or 0
        if ts >= FIRE_WRTE["ts"] or wopr >= FIRE_WRTE["wopr"]:
            return 2
        if ts >= WATCH_WRTE["ts"] or wopr >= WATCH_WRTE["wopr"]:
            return 1
    elif pos == "QB":
        snap = _recent_avg(wk, "snap", OPP_RECENT_WEEKS, lw) or 0
        if snap >= DIAMOND_BACKING["QB"]["snap"]:
            return 1
    return 0


def _diamond_backing_ok(rec, opp):
    lw = opp["last_week"]
    wk = rec["weeks"]
    cond = DIAMOND_BACKING.get(rec["position"])
    if not cond:
        return False
    n = 1 if opp["mode"] == "early" else OPP_RECENT_WEEKS
    snap = _recent_avg(wk, "snap", n, lw) or 0
    if "wtd" in cond:
        return (_recent_avg(wk, "wtd", n, lw) or 0) >= cond["wtd"] and snap >= cond["snap"]
    if "ts" in cond:
        return (_recent_avg(wk, "ts", n, lw) or 0) >= cond["ts"] and snap >= cond["snap"]
    return snap >= cond["snap"]


def evaluate_fa_tag(opp, espn_player, expectation_low, my_weakest_ppg):
    """FA選手のタグを返す: "🔥","👀","💎"(+アーリー時はE付き) or None。"""
    rec = match_player(opp, espn_player)
    if not rec:
        return None, None
    lw = opp["last_week"]
    wk = rec["weeks"]
    pos = rec["position"]
    early = opp["mode"] == "early"
    suffix = "E" if early else ""

    ppg_n = min(PPG_RECENT_WEEKS, lw) if early else PPG_RECENT_WEEKS
    ppg = _recent_avg(wk, "ppr", ppg_n, lw)
    line = opp["startable"].get(pos, 0.0)

    # 💎 放置バリュー: 得点実績あり + 機会裏付け + 最低試合数(1週爆発はフロックと区別不能)
    games = len(_played_weeks(wk, lw))
    if games >= DIAMOND_MIN_GAMES and ppg is not None and \
       (ppg >= line or (my_weakest_ppg is not None and ppg > my_weakest_ppg)):
        if _diamond_backing_ok(rec, opp):
            return "💎" + suffix, rec

    # 🔥/👀 ブレイク前夜: 機会あり + 得点まだ + (上昇中 or 期待乖離)。対象ポジション限定
    if pos not in FIRE_WATCH_POSITIONS:
        return None, rec
    level = _opportunity_level(rec, opp, early)
    if level == 0:
        return None, rec
    points_not_yet = ppg is None or ppg < line
    if not points_not_yet:
        return None, rec
    rz_recent = sum(wk[w]["rz"] for w in _played_weeks(wk, lw)[-2:])
    if early:
        momentum = expectation_low  # 事前期待が低いのに機会がスタメン級=サプライズ
    else:
        momentum = _rising(wk, _main_metric(pos), lw)
    if not momentum:
        return None, rec
    if level == 1 and rz_recent >= RZ_PROMOTE_COUNT:
        level = 2  # RZ機会の連続でウォッチ→本命に昇格
    return ("🔥" if level == 2 else "👀") + suffix, rec


def evaluate_sell_signal(opp, espn_player):
    """自軍選手の⚠️売り時判定。ブローアウト週は系列から除外。"""
    rec = match_player(opp, espn_player)
    if not rec:
        return None
    lw = opp["last_week"]
    key = _main_metric(rec["position"])
    ws = [w for w in _played_weeks(rec["weeks"], lw)
          if (rec["weeks"][w].get("team"), w) not in opp["blowouts"]]
    vals = []
    for w in ws:
        v = rec["weeks"][w].get(key)
        snap = rec["weeks"][w].get("snap")
        if v is not None:
            vals.append((w, v, snap))
    if len(vals) < 3:
        return None
    if vals[-1][0] < lw - 1:
        return None  # 直近週に出場がない(怪我等)なら機会低下シグナルは出さない
    (w0, v0, s0), (w1, v1, s1), (w2, v2, s2) = vals[-3], vals[-2], vals[-1]
    metric_decline = v2 < v1 < v0 and v0 > 0 and (v0 - v2) / v0 >= SELL_DECLINE_RATIO
    snap_decline = (s0 is not None and s1 is not None and s2 is not None
                    and s2 < s1 < s0 and s0 > 0 and (s0 - s2) / s0 >= SELL_DECLINE_RATIO)
    if metric_decline or snap_decline:
        what = "スナップ率" if snap_decline and not metric_decline else (
            "加重機会" if rec["position"] == "RB" else "WOPR")
        return {"reason": f"{what}が2週連続低下 (W{w0}→W{w2})", "weeks": (w0, w1, w2)}
    return None


def evaluate_drop_zone(opp, espn_player, expectation_low):
    """ドロップ2軸マトリクス: safe / hold / watch / core。"""
    rec = match_player(opp, espn_player)
    if not rec:
        # nflverseにデータがない=出場実績なし → 機会低扱い
        return "safe" if expectation_low else "watch"
    level = _opportunity_level(rec, opp, opp["mode"] == "early")
    opp_high = level >= 1
    if expectation_low and not opp_high:
        return "safe"    # ✅ 期待低×機会低 → 即ドロップ可
    if expectation_low and opp_high:
        return "hold"    # 🚫 期待低×機会高 → 保持(🔥相当の掘り出し物)
    if not expectation_low and not opp_high:
        return "watch"   # 🟡 期待高×機会低 → 1週様子見
    return "core"        # 🚫 期待高×機会高


# ------------------------------------------------------------------
# スナップショット統合(エントリポイント)
# ------------------------------------------------------------------

def _espn_recent_ppg(p, upto_week, n=PPG_RECENT_WEEKS):
    """ESPN weekly_actual から直近n週の実PPG。"""
    wa = p.get("weekly_actual") or {}
    vals = []
    for w in sorted(int(k) for k in wa.keys()):
        if w < upto_week and (wa.get(w) is not None or wa.get(str(w)) is not None):
            v = wa.get(w, wa.get(str(w)))
            vals.append(float(v))
    vals = vals[-n:]
    return round(sum(vals) / len(vals), 1) if vals else None


def _expectation_lines(snapshot):
    """ESPN季節予測ベースの「事前期待スタメン級ライン」をポジション別に計算。"""
    pool = {}
    for t in snapshot["teams"]:
        for p in t["roster"]:
            pool.setdefault(p["position"], []).append(_num(p.get("proj_avg")))
    for pos, players in (snapshot.get("free_agents") or {}).items():
        for p in players:
            pool.setdefault(pos, []).append(_num(p.get("proj_avg")))
    lines = {}
    for pos, rank in STARTABLE_RANK.items():
        vals = sorted(pool.get(pos, []), reverse=True)
        lines[pos] = vals[rank - 1] if len(vals) >= rank else (vals[-1] if vals else 0.0)
    return lines


def annotate_snapshot(snapshot, week):
    """スナップショット全体に機会指標・タグ・ドロップゾーンを付与し、要約を返す。

    返り値(レポート用): {"available", "last_week", "mode", "startable",
                        "fa_tagged": [...], "my_sell": [...], "swap_pairs": [...]}
    失敗・データ未公開時は {"available": False}。
    """
    try:
        # OPP_SEASON / OPP_UPTO_WEEK は検証用(過去シーズンのas-of再現)
        season = int(os.environ.get("OPP_SEASON", SEASON))
        upto = os.environ.get("OPP_UPTO_WEEK")
        opp = fetch_opportunity_data(season, int(upto) if upto else None)
    except Exception as e:
        print(f"[warn] 機会指標の構築に失敗(なしで続行): {e}")
        opp = None
    if not opp:
        return {"available": False}

    exp_lines = _expectation_lines(snapshot)

    def expectation_low(p):
        return _num(p.get("proj_avg")) < exp_lines.get(p["position"], 0.0)

    my_team = next(t for t in snapshot["teams"] if t["team_id"] == snapshot["my_team_id"])

    # 自軍の「最弱スタメン実PPG」(ポジショングループ別)。💎の比較基準
    import analysis
    starters, _, _ = analysis.pick_lineup(my_team["roster"], week)
    weakest = {}
    for pos in ("QB", "RB", "WR", "TE"):
        group = [s for s in starters
                 if s["position"] == pos and s["slot"] in (pos, "FLEX")]
        ppgs = [v for v in (_espn_recent_ppg(p, week) for p in group) if v is not None]
        weakest[pos] = min(ppgs) if ppgs else None

    # FAタグ
    fa_tagged = []
    for pos, players in (snapshot.get("free_agents") or {}).items():
        if pos not in OFFENSE_POS:
            continue
        for p in players:
            tag, rec = evaluate_fa_tag(opp, p, expectation_low(p), weakest.get(pos))
            if rec:
                p["opp"] = metrics_summary(opp, rec)
            if tag:
                p["opp_tag"] = tag
                fa_tagged.append(p)
    tag_order = {"💎": 0, "🔥": 1, "👀": 2}
    fa_tagged.sort(key=lambda p: (tag_order.get(p["opp_tag"].rstrip("E"), 9),
                                  -(p.get("opp", {}).get("ppg3") or 0)))

    # 自軍: ⚠️売り時 + ドロップゾーン
    my_sell = []
    for p in my_team["roster"]:
        if p["position"] not in OFFENSE_POS:
            continue
        rec = match_player(opp, p)
        if rec:
            p["opp"] = metrics_summary(opp, rec)
        sell = evaluate_sell_signal(opp, p)
        if sell:
            p["opp_tag"] = "⚠️"
            p["sell_reason"] = sell["reason"]
            my_sell.append(p)
        p["drop_zone"] = evaluate_drop_zone(opp, p, expectation_low(p))

    # 入れ替えペア: 🔥/💎のFA × ✅安全ドロップ
    my_rookie_count = sum(1 for p in my_team["roster"] if p.get("is_rookie"))
    from ff_config import ROOKIE_RULE_UNTIL_WEEK, ROOKIE_MIN_COUNT
    protect_rookies = week <= ROOKIE_RULE_UNTIL_WEEK and my_rookie_count <= ROOKIE_MIN_COUNT

    def value(p):
        """入れ替え評価値: ESPN週予測と直近実PPGの大きい方(基準を添えて返す)。"""
        proj, _ = analysis.player_week_score(p, week)
        ppg = (p.get("opp") or {}).get("ppg3")
        if ppg is None:
            ppg = _espn_recent_ppg(p, week)
        if ppg is not None and ppg > proj:
            return ppg, "直近実績"
        return round(proj, 1), "週予測"

    safe_drops = sorted(
        [p for p in my_team["roster"]
         if p.get("drop_zone") == "safe" and p["position"] not in ("D/ST", "K")
         and not (p.get("is_rookie") and protect_rookies)],
        key=lambda p: value(p)[0])
    watch_drops = sorted(
        [p for p in my_team["roster"]
         if p.get("drop_zone") == "watch" and p["position"] not in ("D/ST", "K")
         and not (p.get("is_rookie") and protect_rookies)],
        key=lambda p: value(p)[0])

    swap_pairs = []
    used_drops = set()
    for fp in fa_tagged:
        if fp["opp_tag"].rstrip("E") not in ("🔥", "💎"):
            continue
        pool = [d for d in safe_drops if d["name"] not in used_drops]
        fallback = False
        if not pool:
            pool = [d for d in watch_drops if d["name"] not in used_drops]
            fallback = True
        if not pool:
            break
        drop = pool[0]
        fv, fb = value(fp)
        dv, db = value(drop)
        if fv - dv <= 0:
            continue  # ネット改善なしの入れ替えは提示しない
        used_drops.add(drop["name"])
        swap_pairs.append({
            "add": fp, "drop": drop, "net": round(fv - dv, 1),
            "add_value": fv, "add_basis": fb, "drop_value": dv, "drop_basis": db,
            "drop_zone_note": "🟡様子見枠から充当" if fallback else "✅安全ドロップ",
        })

    return {
        "available": True, "last_week": opp["last_week"], "mode": opp["mode"],
        "startable": opp["startable"], "fa_tagged": fa_tagged,
        "my_sell": my_sell, "swap_pairs": swap_pairs,
        "small_sample": opp["last_week"] <= 2,
    }
