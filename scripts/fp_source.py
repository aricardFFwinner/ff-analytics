# -*- coding: utf-8 -*-
"""FantasyPros週次投影の取り込み + espn_id突合(P4 §2)。

取得元(実測済み):
  fp_latest_weekly.csv : https://raw.githubusercontent.com/dynastyprocess/data/master/files/fp_latest_weekly.csv
  db_playerids.csv     : https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv

突合は fantasypros_id <-> espn_id のみ(名前によるフォールバックは禁止/仕様§2.2)。
突合できなかった選手は件数+選手名をログに出す(黙って落とさない)。

履歴は docs/data/fp_history/<season>_w<week>.csv に「追記型」で保存する。
同じ週に複数回実行しても、過去に書いた行は消さない(あとから as-of 再現するため)。
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

FP_LATEST_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/fp_latest_weekly.csv"
PLAYERIDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"

# fp_latest_weekly.csv の "pos" 列のうち、このロスター(QB/RB/WR/TE/D-ST/K)で使うもの。
# dl/lb/db(IDP)は対象外(仕様上そもそも使わない)。
RELEVANT_POS = {"QB", "RB", "WR", "TE", "K", "DST"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DOCS_DIR = os.path.join(REPO_ROOT, "docs", "data")

FP_HISTORY_COLUMNS = [
    "generated_at", "fantasypros_id", "espn_id", "player_name", "pos", "team",
    "ecr", "sd", "best", "worst", "pos_rank", "r2p_pts",
    "player_opponent", "player_bye_week", "scrape_date",
]


def _download(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "ff-analytics-p4/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fetch_fp_latest_text():
    return _download(FP_LATEST_URL)


def fetch_playerids_text():
    return _download(PLAYERIDS_URL)


def parse_fp_latest(csv_text):
    """fp_latest_weekly.csv をパースし、対象ポジションの行だけ返す。"""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        if (row.get("pos") or "").strip() not in RELEVANT_POS:
            continue
        fid = (row.get("fantasypros_id") or "").strip()
        if not fid or fid == "NA":
            continue
        rows.append(row)
    return rows


def load_playerid_map(csv_text):
    """fantasypros_id(str) -> espn_id(str) の突合表を作る。

    禁止事項: 名前によるフォールバックは一切行わない。IDが両方揃っている行のみ採用。
    """
    mapping = {}
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        fid = (row.get("fantasypros_id") or "").strip()
        eid = (row.get("espn_id") or "").strip()
        if not fid or fid == "NA" or not eid or eid == "NA":
            continue
        mapping[fid] = eid
    return mapping


def match(fp_rows, id_map):
    """fp_rows を espn_id で突合する。

    戻り値: (matched, unmatched)
      matched: 各行に "espn_id" を足したリスト
      unmatched: [{"fantasypros_id":.., "player_name":.., "pos":..}, ...]
    """
    matched, unmatched = [], []
    for row in fp_rows:
        fid = (row.get("fantasypros_id") or "").strip()
        eid = id_map.get(fid)
        if eid is None:
            unmatched.append({
                "fantasypros_id": fid,
                "player_name": row.get("player_name"),
                "pos": row.get("pos"),
            })
            continue
        matched.append({**row, "espn_id": eid})
    return matched, unmatched


def save_fp_history(season, week, matched_rows, generated_at, docs_dir=None):
    """docs/data/fp_history/<season>_w<week>.csv に追記保存する(上書きしない)。"""
    docs_dir = docs_dir or DEFAULT_DOCS_DIR
    hist_dir = os.path.join(docs_dir, "fp_history")
    os.makedirs(hist_dir, exist_ok=True)
    path = os.path.join(hist_dir, f"{season}_w{week}.csv")

    file_exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FP_HISTORY_COLUMNS)
        if not file_exists:
            writer.writeheader()
        for row in matched_rows:
            writer.writerow({
                "generated_at": generated_at,
                "fantasypros_id": row.get("fantasypros_id"),
                "espn_id": row.get("espn_id"),
                "player_name": row.get("player_name"),
                "pos": row.get("pos"),
                "team": row.get("team"),
                "ecr": row.get("ecr"),
                "sd": row.get("sd"),
                "best": row.get("best"),
                "worst": row.get("worst"),
                "pos_rank": row.get("pos_rank"),
                "r2p_pts": row.get("r2p_pts"),
                "player_opponent": row.get("player_opponent"),
                "player_bye_week": row.get("player_bye_week"),
                "scrape_date": row.get("scrape_date"),
            })
    return path


def load_fp_history(season, week, docs_dir=None):
    """保存済みの fp_history/<season>_w<week>.csv を読む(存在しなければ None)。

    同じ週に複数回分の generated_at が積まれている場合、
    fantasypros_id ごとに最新の generated_at の行だけを返す。
    """
    docs_dir = docs_dir or DEFAULT_DOCS_DIR
    path = os.path.join(docs_dir, "fp_history", f"{season}_w{week}.csv")
    if not os.path.exists(path):
        return None
    latest = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fid = row.get("fantasypros_id")
            prev = latest.get(fid)
            if prev is None or (row.get("generated_at") or "") >= (prev.get("generated_at") or ""):
                latest[fid] = row
    return list(latest.values())


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.upper() == "NA":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def by_espn_id(rows):
    """espn_id(str) -> {r2p_pts, ecr, sd, best, worst, pos_rank, ...(数値化済み)} の辞書に変換。"""
    out = {}
    for row in rows:
        eid = row.get("espn_id")
        if not eid:
            continue
        out[str(eid)] = {
            "r2p_pts": _num(row.get("r2p_pts")),
            "ecr": _num(row.get("ecr")),
            "sd": _num(row.get("sd")),
            "best": _num(row.get("best")),
            "worst": _num(row.get("worst")),
            "pos_rank": row.get("pos_rank"),
            "player_opponent": row.get("player_opponent"),
            "player_bye_week": row.get("player_bye_week"),
        }
    return out


def run(season, week, docs_dir=None, fp_csv_text=None, playerids_csv_text=None):
    """毎週の運用フロー: 取得 → 突合 → 履歴保存。

    fp_csv_text / playerids_csv_text を渡すとネットワークを使わない(テスト用)。
    戻り値: {"matched": n, "unmatched": n, "unmatched_names": [...],
             "by_espn_id": {...}, "history_path": path}
    """
    fp_text = fp_csv_text if fp_csv_text is not None else fetch_fp_latest_text()
    pid_text = playerids_csv_text if playerids_csv_text is not None else fetch_playerids_text()

    fp_rows = parse_fp_latest(fp_text)
    id_map = load_playerid_map(pid_text)
    matched, unmatched = match(fp_rows, id_map)

    generated_at = datetime.now(JST).isoformat()
    history_path = save_fp_history(season, week, matched, generated_at, docs_dir=docs_dir)

    if unmatched:
        names = ", ".join(f'{u["player_name"]}({u["pos"]})' for u in unmatched[:30])
        more = f" 他{len(unmatched)-30}件" if len(unmatched) > 30 else ""
        print(f"[warn] fp_source: ID突合失敗 {len(unmatched)}件: {names}{more}")
    print(f"[info] fp_source: 突合成功 {len(matched)}件 / 失敗 {len(unmatched)}件 (week={week})")

    return {
        "matched": len(matched),
        "unmatched": len(unmatched),
        "unmatched_names": [u["player_name"] for u in unmatched],
        "by_espn_id": by_espn_id(matched),
        "history_path": history_path,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python3 fp_source.py <season> <week>")
        sys.exit(1)
    result = run(int(sys.argv[1]), int(sys.argv[2]))
    print(json.dumps({k: v for k, v in result.items() if k != "by_espn_id"}, ensure_ascii=False, indent=1))
