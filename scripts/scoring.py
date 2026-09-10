# -*- coding: utf-8 -*-
"""選手粒度の自己採点ログ(P4 §4.1, §4.2, §4.4, §4.7 Phase0範囲)。

このファイルが担当するのは Phase 0 の範囲のみ:
  - record_player_preds(): 火曜の記録(§4.1/4.2)
  - compute_score()       : 取り逃した点の3指標(§4.4)
  - backfill_week()       : 後追い採点(§4.7)

Phase 1 以降(月曜の自動採点処理・原因分類§4.5・併記モード)はここに含めない。
推奨ロジック(analysis.player_week_score / pick_lineup)は一切変更しない。
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ff_config

JST = timezone(timedelta(hours=9))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVAL_LOG_PATH = os.path.join(REPO_ROOT, "docs", "data", "eval_log.json")
DEFAULT_TX_PATH = os.path.join(REPO_ROOT, "docs", "data", "transactions.json")

# 2026シーズンの唯一の既知アンカー(仕様書§7に明記): W2の記録締め切りは 2026-09-15 18:17 JST。
# これがそのまま「W2のas-of(登録締め日)」でもある。他の週はここから7日単位でずらして求める。
ANCHOR_SEASON = 2026
ANCHOR_WEEK = 2
ANCHOR_ASOF_JST = datetime(2026, 9, 15, 18, 17, tzinfo=JST)


# ---------------------------------------------------------------------------
# eval_log.json の読み書き
# ---------------------------------------------------------------------------

def load_eval_log(path=None):
    path = path or DEFAULT_EVAL_LOG_PATH
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_eval_log(log, path=None):
    path = path or DEFAULT_EVAL_LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    return path


# ---------------------------------------------------------------------------
# §4.1 / §4.2: 火曜の記録
# ---------------------------------------------------------------------------

def _slot_labels(starters):
    """pick_lineup()が返す slot("QB"/"RB"/.../"FLEX")に、同枠内の通し番号を振る。

    仕様§4.1の例("slot": "RB1")に合わせるための表示用整形のみ。
    推奨ロジック自体(どの選手を選ぶか)には一切関与しない。
    """
    counts = {}
    labels = {}
    for s in starters:
        slot = s.get("slot", "")
        counts[slot] = counts.get(slot, 0) + 1
        labels[s.get("id") or s.get("name")] = f'{slot}{counts[slot]}'
    return labels


def build_player_preds_entry(week, generated_at, roster, starters, fp_by_espn_id=None):
    """eval_log[week] に書く player_preds / rec_lineup を作る(I/Oなし・純粋関数)。

    roster: player dict のリスト(id/name/position/pro_team/score等を含む)。
            id は espn_id 相当のキー(str化して扱う)。
    starters: analysis.pick_lineup() が返す starters(score/slotを含む)。
    fp_by_espn_id: fp_source.by_espn_id() の戻り値(無ければ None のまま)。
    """
    fp_by_espn_id = fp_by_espn_id or {}
    starter_ids = {str(s.get("id")) for s in starters}
    slot_labels = _slot_labels(starters)

    player_preds = {}
    for p in roster:
        pid = str(p.get("id"))
        fp = fp_by_espn_id.get(pid)
        espn_val = p.get("score")  # analysis.player_week_score()が既に計算した値(Phase0では変更しない)
        player_preds[pid] = {
            "name": p.get("name"),
            "pos": p.get("position"),
            "pro_team": p.get("pro_team"),
            "src": {
                "espn": espn_val,
                "fp": fp.get("r2p_pts") if fp else None,
                "blend": None,       # Phase1(combine.py)で埋める。Phase0は未実装
                "vegas_scale": None,  # 保留(仕様§3.5)
            },
            "combined": espn_val,  # Phase0は推奨ロジック不変=ESPN週次予測がそのまま「合成後」の値
            "sd": None,             # Phase3で埋める
            "slot": slot_labels.get(pid),
            "rec_start": pid in starter_ids,
            "snap_pct_prior": p.get("snap_pct_prior"),  # 未配線。取得できなければNoneのまま(推測)
        }

    rec_lineup = [str(s.get("id")) for s in starters]
    return {
        "generated_at": generated_at,
        "player_preds": player_preds,
        "rec_lineup": rec_lineup,
        "rec_lineup_alt": None,  # Phase1(併記モード)の範囲。Phase0では常にNone
        "actual_lineup": None,
        "actual": {},
        "score": {},
    }


def record_player_preds(season, week, generated_at, roster, starters, fp_by_espn_id=None,
                         path=None, eval_log=None):
    """火曜18:17の記録本体。既存の同週記録(backfillでない本記録)は上書きしない(§4.2)。

    eval_log を渡した場合はその dict を直接更新して返す(保存はしない=テスト用)。
    渡さない場合は path(既定 docs/data/eval_log.json)から読み込み、更新して保存する。
    """
    own_log = eval_log is None
    log = eval_log if eval_log is not None else load_eval_log(path)

    key = str(week)
    existing = log.get(key)
    if existing and existing.get("player_preds") and not existing.get("backfilled"):
        print(f"[info] scoring.record_player_preds: week{week}は既に本記録あり。上書きしない")
        return {"status": "skipped_exists", "week": week}

    entry = build_player_preds_entry(week, generated_at, roster, starters, fp_by_espn_id)
    merged = dict(existing or {})
    # 既存の preds キー(チーム粒度)は残す(後方互換)
    merged.update(entry)
    if existing and "preds" in existing:
        merged["preds"] = existing["preds"]
    # backfill専用メタは本記録では持たない
    merged.pop("backfilled", None)
    merged.pop("backfill_generated_at", None)
    merged.pop("asof", None)
    merged.pop("roster_reconstructed", None)
    log[key] = merged

    if own_log:
        save_eval_log(log, path)
    print(f"[info] scoring.record_player_preds: week{week} 記録完了 (選手{len(entry['player_preds'])}人)")
    return {"status": "recorded", "week": week, "n_players": len(entry["player_preds"])}


# ---------------------------------------------------------------------------
# §4.4: 取り逃した点(3指標)
# ---------------------------------------------------------------------------

def _greedy_optimal_lineup(scored_players, slots=None, flex_eligible=None):
    """analysis.pick_lineup()と同じ「専用枠→FLEX」貪欲法で最大得点の組み合わせを作る。

    scored_players: [{"id":.., "position":.., "score": float}, ...]
    ff_config.STARTER_SLOTS の構成(専用枠1つのFLEXのみ)ではこの貪欲法が最適
    (analysis.pick_lineup()のコメントに明記されている前提を踏襲)。
    """
    slots = dict(slots or ff_config.STARTER_SLOTS)
    flex_eligible = flex_eligible or ff_config.FLEX_ELIGIBLE
    scored = sorted(scored_players, key=lambda x: x["score"], reverse=True)

    used = set()
    total = 0.0
    chosen = []
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        need = slots.get(pos, 0)
        for p in scored:
            if need == 0:
                break
            if p["id"] in used or p["position"] != pos:
                continue
            chosen.append(p["id"])
            used.add(p["id"])
            total += p["score"]
            need -= 1
    for p in scored:
        if p["id"] in used or p["position"] not in flex_eligible:
            continue
        chosen.append(p["id"])
        used.add(p["id"])
        total += p["score"]
        break
    return round(total, 2), chosen


def compute_score(roster_ids_positions, actual_points_by_id, rec_lineup_ids, actual_lineup_ids=None,
                   slots=None, flex_eligible=None):
    """§4.4の3指標を計算する(純粋関数、I/Oなし)。

    roster_ids_positions: [{"id":.., "position":..}, ...] その週にロスターにいた全選手
    actual_points_by_id  : {id(str): 実得点(float) or None}
    rec_lineup_ids        : 推奨した布陣のid一覧
    actual_lineup_ids     : 実際にユーザーが出した布陣のid一覧(Noneなら実行の質は計算しない)
    """
    def pts(pid):
        v = actual_points_by_id.get(str(pid))
        return float(v) if v is not None else 0.0

    scored = [{"id": str(r["id"]), "position": r["position"], "score": pts(r["id"])}
              for r in roster_ids_positions]
    optimal_pts, optimal_ids = _greedy_optimal_lineup(scored, slots=slots, flex_eligible=flex_eligible)

    rec_pts = round(sum(pts(pid) for pid in rec_lineup_ids), 2)

    actual_pts = None
    exec_quality = None
    if actual_lineup_ids is not None:
        actual_pts = round(sum(pts(pid) for pid in actual_lineup_ids), 2)
        exec_quality = round(rec_pts - actual_pts, 2)

    rec_quality = round(optimal_pts - rec_pts, 2)

    return {
        "optimal_pts": optimal_pts,
        "optimal_lineup": optimal_ids,
        "rec_pts": rec_pts,
        "actual_pts": actual_pts,
        "rec_quality": rec_quality,   # optimal_pts - rec_pts (仕様§4.4の主指標「推奨の質」)
        "exec_quality": exec_quality,  # rec_pts - actual_pts (「実行の質」)
    }


# ---------------------------------------------------------------------------
# §4.7: 後追い採点(backfill)
# ---------------------------------------------------------------------------

def week_asof_jst(season, week):
    """当該週の登録締め日(火曜18:17 JST)を返す。

    2026シーズンのみ対応。仕様書§7に明記された「W2記録前=2026-09-15 18:17 JST」を
    唯一のアンカーとして7日単位でずらす((推測)を含まない確定値からの機械的な計算)。
    2024/2025年(バックテスト§6の範囲)はここでは扱わない。
    """
    if season != ANCHOR_SEASON:
        raise ValueError(
            f"week_asof_jst: season={season}の自動算出は未対応(Phase0は{ANCHOR_SEASON}年のみ)。"
            " asofを明示的に指定してbackfill_week()を呼ぶこと。"
        )
    return ANCHOR_ASOF_JST + timedelta(days=7 * (week - ANCHOR_WEEK))


def _iter_raw_players(schedule):
    """probe_history.py と同じ生JSON走査(重複実装ではなく同一パターンの流用)。"""
    for matchup in schedule or []:
        for side in ("home", "away"):
            team = matchup.get(side)
            if not team:
                continue
            roster = (team.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
            for entry in roster:
                player = (entry.get("playerPoolEntry") or {}).get("player") or entry.get("player")
                if player:
                    yield side, team, player


def _espn_week_projections(lg, week, my_team_id):
    """box_view(mMatchupScore+mScoreboard, statSourceId=1/statSplitTypeId=1)から
    自チーム選手の週次投影を取る。1週=1リクエスト(仕様の上限を守る)。
    """
    params = {"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": week}
    data = lg.espn_request.league_get(params=params)
    schedule = data.get("schedule", [])
    proj = {}
    for side, team, player in _iter_raw_players(schedule):
        if team.get("teamId") != my_team_id:
            continue
        pid = str(player.get("id"))
        val = None
        for st in player.get("stats") or []:
            if (st.get("statSourceId") == 1 and st.get("statSplitTypeId") == 1
                    and st.get("scoringPeriodId") == week):
                val = st.get("appliedTotal")
                break
        proj[pid] = val
    return proj


def _find_my_lineup(boxes, my_team_id):
    for m in boxes:
        home_id = getattr(getattr(m, "home_team", None), "team_id", None)
        away_id = getattr(getattr(m, "away_team", None), "team_id", None)
        if home_id == my_team_id:
            return m.home_lineup
        if away_id == my_team_id:
            return m.away_lineup
    return None


def _reconstruct_roster(current_rows, my_team_name, asof_dt, tx_path=None):
    """txlogの逆適用でasof時点のロスターを復元する(ベストエフォート)。

    対応できるのは「asof以降に自チームが追加した選手を取り消す」ケースのみ。
    DROPの取り消し(=選手を復元)は、当時のposition等のメタ情報が
    transactions.json に無いため対応しない(reconstructed=Falseにフォールバック)。
    """
    tx_path = tx_path or DEFAULT_TX_PATH
    try:
        with open(tx_path, encoding="utf-8") as f:
            log = json.load(f)
    except Exception as e:
        return current_rows, False, f"transactions.json読み込み不可({e})。現ロスターで代用"

    def _tx_dt(e):
        d = e.get("date")
        try:
            return datetime.fromtimestamp(int(d) / 1000.0, tz=timezone.utc)
        except Exception:
            return None

    relevant = []
    for e in log.get("entries") or []:
        if e.get("team") != my_team_name:
            continue
        dt = _tx_dt(e)
        if dt is not None and dt >= asof_dt:
            relevant.append((e, dt))

    if not relevant:
        return current_rows, False, "asof以降の自チーム取引記録なし。現ロスターで代用"

    relevant.sort(key=lambda x: x[1], reverse=True)
    rows_by_name = {r["name"]: dict(r) for r in current_rows}
    for e, dt in relevant:
        action = (e.get("action") or "").upper()
        player = e.get("player")
        if not player:
            continue
        if "DROP" in action:
            return current_rows, False, f"DROPの取り消し(選手復元)は未対応のためフォールバック: {player}"
        if any(k in action for k in ("ADD", "CLAIM", "WAIVER", "ACQUIRED")):
            rows_by_name.pop(player, None)
            continue
        return current_rows, False, f"未対応の取引種別のためフォールバック: {action}"

    return list(rows_by_name.values()), True, f"txlog逆適用: 自チーム取引{len(relevant)}件を取り消し"


def _load_fp_for_backfill(season, week, asof_dt, docs_dir=None, parquet_path=None):
    """fp_history優先、無ければdb_fpecr.parquetをasofで切って使う。

    parquet側の週次ページ(weekly-qb/rb/wr/te/k/dst)はecr_type='wp'のみで
    構成されている(2026-09-10時点で実測確認済み)ため、ecr_type混在は起きないが、
    念のため 'wp' 以外が混入していないかを検査し、混入時はエラーにする(§6.1④)。
    """
    import fp_source
    rows = fp_source.load_fp_history(season, week, docs_dir=docs_dir)
    if rows:
        return fp_source.by_espn_id(rows), "fp_history"

    if not parquet_path or not os.path.exists(parquet_path):
        print(f"[warn] backfill_week: FP復元不可(fp_historyなし、parquet未指定/未取得)。src.fpはNoneのまま進む")
        return {}, "none"

    import pandas as pd
    weekly_pages = {"weekly-qb", "weekly-rb", "weekly-wr", "weekly-te", "weekly-k", "weekly-dst"}
    df = pd.read_parquet(parquet_path, columns=["page_type", "ecr_type", "id", "ecr", "sd", "best", "worst", "scrape_date"])
    df = df[df["page_type"].isin(weekly_pages)]
    bad = df[df["ecr_type"] != "wp"]
    if len(bad):
        raise RuntimeError(
            f"backfill_week: 週次ページなのにecr_type!='wp'が{len(bad)}件混入(仕様§6.1④違反の疑い)。処理を停止"
        )
    asof_date = asof_dt.astimezone(timezone.utc).date().isoformat()
    df = df[df["scrape_date"] <= asof_date]
    if df.empty:
        print(f"[warn] backfill_week: asof({asof_date})以前のFPスナップショットが無い週。src.fpはNoneのまま進む")
        return {}, "none"
    # fantasypros_id毎に、asof以前で最も新しいscrape_dateの行を採用(先読み防止のas-ofルール)
    df = df.sort_values("scrape_date").groupby("id", as_index=False).last()

    id_map_text = None
    playerids_path = os.environ.get("PLAYERIDS_CSV_PATH")
    if playerids_path and os.path.exists(playerids_path):
        with open(playerids_path, encoding="utf-8") as f:
            id_map_text = f.read()
    else:
        id_map_text = fp_source.fetch_playerids_text()
    id_map = fp_source.load_playerid_map(id_map_text)

    out = {}
    unmatched = 0
    for _, row in df.iterrows():
        fid = str(row["id"])
        eid = id_map.get(fid)
        if not eid:
            unmatched += 1
            continue
        out[str(eid)] = {
            "r2p_pts": None,  # parquetにはr2p_ptsが無い(ECR/sd/best/worstのみ復元可能)
            "ecr": float(row["ecr"]) if row["ecr"] is not None else None,
            "sd": float(row["sd"]) if row["sd"] is not None else None,
            "best": float(row["best"]) if row["best"] is not None else None,
            "worst": float(row["worst"]) if row["worst"] is not None else None,
        }
    if unmatched:
        print(f"[warn] backfill_week(parquet経路): ID突合失敗 {unmatched}件")
    print(f"[info] backfill_week: FPをdb_fpecr.parquetから復元(asof<={asof_date}, {len(out)}名, r2p_ptsなし)")
    return out, "parquet"


def backfill_week(season, week, asof=None, path=None, parquet_path=None, docs_dir=None,
                   league_id=None, espn_s2=None, swid=None):
    """§4.7の後追い採点本体。ESPNへの追加リクエストは box_view 1回 + box_scores 1回のみ。"""
    import analysis  # 推奨ロジックは変更しない。既存関数をそのまま使う

    asof = asof or week_asof_jst(season, week)

    league_id = league_id or int(os.environ["LEAGUE_ID"].strip())
    espn_s2 = espn_s2 or os.environ["ESPN_S2"].strip()
    swid = swid or os.environ["SWID"].strip()
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"

    from espn_api.football import League
    lg = League(league_id=league_id, year=season, espn_s2=espn_s2, swid=swid)

    my_team = next(t for t in lg.teams if t.team_id == ff_config.MY_TEAM_ID)
    my_team_name = getattr(my_team, "team_name", "")

    # --- box_scores 1回: 実際の布陣・実得点(このロスター自体が「その週にいた選手」の正) ---
    boxes = lg.box_scores(week)
    lineup = _find_my_lineup(boxes, ff_config.MY_TEAM_ID)
    if lineup is None:
        raise RuntimeError(f"backfill_week: week{week}のbox_scoresに自チームのlineupが見つからない")

    current_rows, actual_points_by_id, actual_lineup_ids = [], {}, []
    for p in lineup:
        pid = str(getattr(p, "playerId", None))
        pts = getattr(p, "points", None)
        slot = getattr(p, "slot_position", "")
        current_rows.append({
            "id": pid, "name": getattr(p, "name", "?"),
            "position": getattr(p, "position", "?"),
            "pro_team": getattr(p, "proTeam", "?"),
        })
        actual_points_by_id[pid] = pts
        if slot not in ("BE", "IR"):
            actual_lineup_ids.append(pid)

    # --- 火曜時点のロスター: txlog逆適用(ベストエフォート、不可なら現ロスター+false) ---
    roster_rows, roster_reconstructed, roster_note = _reconstruct_roster(current_rows, my_team_name, asof)
    print(f"[info] backfill_week: ロスター復元 -> {roster_note}")

    # --- box_view 1回: ESPN週次予測 ---
    proj_raw = _espn_week_projections(lg, week, ff_config.MY_TEAM_ID)

    # --- FP: fp_history優先、無ければparquet(asof切り) ---
    fp_by_espn_id, fp_src = _load_fp_for_backfill(season, week, asof, docs_dir=docs_dir, parquet_path=parquet_path)

    # --- 推奨ロジックは変えない。analysis.pick_lineup()をそのまま使う ---
    fake_roster = []
    for r in roster_rows:
        wp = {}
        pv = proj_raw.get(r["id"])
        if pv is not None:
            wp[week] = pv
        fake_roster.append({**r, "weekly_proj": wp, "injury_status": ""})
    starters, bench, close_calls = analysis.pick_lineup(fake_roster, week)
    rec_lineup_ids = [str(s.get("id")) for s in starters]

    generated_at = datetime.now(JST).isoformat()
    entry = build_player_preds_entry(week, generated_at, fake_roster, starters, fp_by_espn_id)
    entry["rec_lineup"] = rec_lineup_ids
    entry["backfilled"] = True
    entry["backfill_generated_at"] = generated_at
    entry["asof"] = asof.isoformat()
    entry["roster_reconstructed"] = roster_reconstructed
    entry["actual_lineup"] = actual_lineup_ids
    entry["actual"] = actual_points_by_id

    have_actuals = any(v is not None for v in actual_points_by_id.values())
    if have_actuals:
        entry["score"] = compute_score(
            roster_ids_positions=[{"id": r["id"], "position": r["position"]} for r in roster_rows],
            actual_points_by_id=actual_points_by_id,
            rec_lineup_ids=rec_lineup_ids,
            actual_lineup_ids=actual_lineup_ids,
        )
    else:
        entry["score"] = {}  # 未確定。月曜の採点処理(Phase1)が後で埋める

    log = load_eval_log(path)
    key = str(week)
    existing = log.get(key)
    if existing and existing.get("player_preds") and not existing.get("backfilled"):
        print(f"[info] backfill_week: week{week}は既に本記録(backfillでない)がある。上書きしない")
        return {"status": "skipped_real_record_exists", "week": week}

    merged = dict(existing or {})
    merged.update(entry)
    if existing and "preds" in existing:
        merged["preds"] = existing["preds"]
    log[key] = merged
    save_eval_log(log, path)

    print(f"[info] backfill_week: week{week} 後追い記録完了 "
          f"(fp_src={fp_src}, roster_reconstructed={roster_reconstructed}, "
          f"score={'あり' if have_actuals else '未確定'})")
    return {
        "status": "backfilled", "week": week, "fp_source": fp_src,
        "roster_reconstructed": roster_reconstructed, "n_players": len(fake_roster),
        "score": entry["score"],
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python3 scoring.py <season> <week> [asof_iso]")
        sys.exit(1)
    _season, _week = int(sys.argv[1]), int(sys.argv[2])
    _asof = None
    if len(sys.argv) > 3:
        _asof = datetime.fromisoformat(sys.argv[3])
    result = backfill_week(_season, _week, asof=_asof)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
