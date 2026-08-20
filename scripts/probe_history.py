# -*- coding: utf-8 -*-
"""過去シーズン(2024/2025)の遡り可否チェック(手動実行用)。

確認対象:
  1. ドラフトログ(League.draft)
  2. トランザクション履歴(League.recent_activity)
  3. 各週のスタメン/ベンチ(League.box_scores)
  4. 週次「予測値」が当時のまま残っているか(probe_projections)
     ※ これは「取れた/取れない」の結論を出さない。ESPNの生JSONを
     そのままダンプするだけ。ESPNが過去の週に"今の"評価を遡って
     返してきている疑いを、人間が生データを見て判定するための材料。
1〜3の結果はActionsのログに出力するだけ(ファイルは書かない)。
4の結果はログ出力に加えてdocs/data/probe_espn_proj.jsonにも保存する。
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ_OUT_JSON = os.path.join(REPO_ROOT, "docs", "data", "probe_espn_proj.json")
PROJ_PROBE_WEEKS = (5, 10)
PROJ_SAMPLE_SIZE = 5


def probe(year):
    from espn_api.football import League
    print("=" * 60)
    print(f"### {year}シーズン ###")
    try:
        lg = League(
            league_id=int(os.environ["LEAGUE_ID"].strip()),
            year=year,
            espn_s2=os.environ["ESPN_S2"].strip(),
            swid=os.environ["SWID"].strip(),
        )
        print(f"[OK] リーグ接続: {lg.settings.name} / チーム数{len(lg.teams)}")
    except Exception as e:
        print(f"[NG] リーグ接続失敗: {e}")
        return

    # 1. ドラフト
    try:
        draft = lg.draft
        print(f"[{'OK' if draft else 'NG'}] ドラフト: {len(draft)}ピック取得")
        for pick in draft[:4]:
            team = getattr(getattr(pick, "team", None), "team_name", "?")
            print(f"     R{getattr(pick, 'round_num', '?')}.{getattr(pick, 'round_pick', '?')} "
                  f"{getattr(pick, 'playerName', '?')} → {team}")
    except Exception as e:
        print(f"[NG] ドラフト取得失敗: {e}")

    # 2. トランザクション
    try:
        acts = lg.recent_activity(size=25)
        print(f"[{'OK' if acts else 'NG'}] トランザクション: {len(acts)}件取得")
        for a in acts[:3]:
            for tup in getattr(a, "actions", [])[:1]:
                team = getattr(tup[0], "team_name", "?")
                player = getattr(tup[2], "name", tup[2]) if len(tup) > 2 else "?"
                print(f"     {team}: {tup[1] if len(tup) > 1 else '?'} {player}")
    except Exception as e:
        print(f"[NG] トランザクション取得失敗: {e}")

    # 3. 週次スタメン/ベンチ(W1とW5で確認)
    for wk in (1, 5):
        try:
            boxes = lg.box_scores(wk)
            n_players = sum(len(b.home_lineup) + len(b.away_lineup) for b in boxes)
            sample = ""
            if boxes and boxes[0].home_lineup:
                p = boxes[0].home_lineup[0]
                sample = (f" 例: {getattr(p, 'name', '?')} slot={getattr(p, 'slot_position', '?')} "
                          f"実{getattr(p, 'points', '?')}pt")
            print(f"[{'OK' if n_players else 'NG'}] W{wk}スタメン: {len(boxes)}試合/{n_players}選手{sample}")
        except Exception as e:
            print(f"[NG] W{wk}スタメン取得失敗: {e}")


def _iter_raw_players(schedule):
    """mMatchupScore/mScoreboard viewの生JSON(data['schedule'])から
    (side, teamの生dict, playerの生dict) を列挙するだけのイテレータ。
    加工・フィルタは一切しない(空スロット・IR等はplayerが無いのでスキップするのみ)。
    """
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


def _print_raw_stat_entries(label, raw_player):
    """statsエントリの主要キーをそのまま(加工せず)標準出力に出す。
    appliedTotal/appliedAverageが実測値(statSourceId=0)と予測値(statSourceId=1想定)の
    両方見えているか、scoringPeriodId/seasonIdが要求した週・年と一致しているかを
    人間が目視確認できるようにするための出力。
    """
    name = raw_player.get("fullName", "?")
    pid = raw_player.get("id", "?")
    pos_id = raw_player.get("defaultPositionId", "?")
    print(f"  [{label}] {name} (id={pid}, defaultPositionId={pos_id})")
    stats = raw_player.get("stats") or []
    if not stats:
        print("      statsエントリなし(=このplayerには生statsが1件も無い)")
        return
    for st in stats:
        print(
            "      statSourceId={} statSplitTypeId={} scoringPeriodId={} seasonId={} "
            "id={!r} appliedTotal={} appliedAverage={} proTeamId={}".format(
                st.get("statSourceId"), st.get("statSplitTypeId"), st.get("scoringPeriodId"),
                st.get("seasonId"), st.get("id"), st.get("appliedTotal"), st.get("appliedAverage"),
                st.get("proTeamId"),
            )
        )


def probe_projection_week(lg, year, week):
    """指定scoringPeriodIdについて、2つの異なるviewで生statsを取得する。

    (A) mMatchupScore/mScoreboard view: League.box_scores()が内部で使っているのと同じview。
        rosterForCurrentScoringPeriodはscoringPeriodIdパラメータに従って組まれるため、
        matchupPeriodでの絞り込みはせず全スケジュールから走査する(box_scores()の
        matchup_period解決ロジックには依存しない、より単純で壊れにくい経路)。
    (B) kona_playercard view: League.player_info()が内部で使っているview。
        (A)で見つかった選手IDを流用し、同じ選手・同じ週について別viewでの生statsを
        突き合わせられるようにする(2経路が食い違えば、それ自体が重要な証拠になる)。

    どちらの結果も抽出・加工をせず生JSONのままdictに詰めて返す。
    """
    result = {"year": year, "week": week, "box_view": None, "playercard_view": None, "errors": []}

    try:
        params = {"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": week}
        data = lg.espn_request.league_get(params=params)
        schedule = data.get("schedule", [])
        samples = []
        for side, team, player in _iter_raw_players(schedule):
            if len(samples) >= PROJ_SAMPLE_SIZE:
                break
            samples.append({
                "matchup_side": side,
                "team_id": team.get("teamId"),
                "team_totalPoints": team.get("totalPoints"),
                "team_totalPointsLive": team.get("totalPointsLive"),
                "team_totalProjectedPointsLive": team.get("totalProjectedPointsLive"),
                "raw_player": player,  # 生JSON。一切加工なし
            })
        result["box_view"] = {
            "request_view": params["view"],
            "scoringPeriodId": week,
            "schedule_matchup_count": len(schedule),
            "sample_count": len(samples),
            "samples": samples,
        }
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[NG] {year} W{week} mMatchupScore取得失敗: {msg}")
        print(traceback.format_exc())
        result["errors"].append({"stage": "box_view", "error": msg, "traceback": traceback.format_exc()})

    try:
        player_ids = []
        if result["box_view"]:
            for s in result["box_view"]["samples"]:
                pid = s["raw_player"].get("id")
                if pid is not None:
                    player_ids.append(pid)
        if player_ids:
            card_data = lg.espn_request.get_player_card(player_ids, lg.finalScoringPeriod)
            result["playercard_view"] = {
                "request_view": "kona_playercard",
                "filterStatsForTopScoringPeriodIds_value": lg.finalScoringPeriod,
                "additionalValue": [f"00{year}", f"10{year}"],
                "players": card_data.get("players", []),  # 生JSON。一切加工なし
            }
        else:
            result["playercard_view"] = {"skipped": "(A)でplayer_idが1件も取れず未実行"}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[NG] {year} W{week} kona_playercard取得失敗: {msg}")
        print(traceback.format_exc())
        result["errors"].append({"stage": "playercard_view", "error": msg, "traceback": traceback.format_exc()})

    return result


def probe_projections():
    """本命プローブ: 過去シーズンの週次「予測値」が当時のまま残っているかを生データで確認する。

    このスクリプトは「取れた/取れない」の判定を出さない。
    ESPNは予測値が無いと返すのではなく、現在(2026年)の評価を過去の週に遡って
    返してくる可能性があるため、その場合レスポンスに数字は入っておりそれだけでは
    「取れた」と誤判定してしまう。したがって生のstatsエントリ
    (statSourceId/statSplitTypeId/scoringPeriodId/seasonId/appliedTotal/appliedAverage等)を
    標準出力とJSONの両方にそのまま出し、判定は人間(サンドボックス側でFantasyProsと
    突き合わせる後続処理)に委ねる。
    """
    print("=" * 60)
    print("### 予測値プローブ(過去シーズンの週次projected points、生データダンプ) ###")
    print(f"対象週(scoringPeriodId): {PROJ_PROBE_WEEKS}")
    print("=" * 60)

    all_results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks_probed": list(PROJ_PROBE_WEEKS),
        "note": (
            "判定なし。statSourceId=0が実測、1が予測という espn_api ライブラリの解釈に基づき"
            "生のstatsエントリをそのまま格納している。scoringPeriodId/seasonIdが要求値と"
            "一致しているか、appliedTotal(予測想定)が実測と酷似していないか等は目視で確認すること。"
        ),
        "seasons": {},
    }

    for year in (2024, 2025):
        print(f"--- {year}シーズン ---")
        try:
            from espn_api.football import League
            lg = League(
                league_id=int(os.environ["LEAGUE_ID"].strip()),
                year=year,
                espn_s2=os.environ["ESPN_S2"].strip(),
                swid=os.environ["SWID"].strip(),
            )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[NG] {year}リーグ接続失敗: {msg}")
            print(traceback.format_exc())
            all_results["seasons"][str(year)] = {"connect_error": msg, "traceback": traceback.format_exc()}
            continue

        season_result = {"weeks": {}}
        for week in PROJ_PROBE_WEEKS:
            print(f"-- {year} scoringPeriodId={week} --")
            try:
                wk_result = probe_projection_week(lg, year, week)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                print(f"[NG] {year} W{week} 予期しない失敗: {msg}")
                print(traceback.format_exc())
                wk_result = {"unexpected_error": msg, "traceback": traceback.format_exc()}
            season_result["weeks"][str(week)] = wk_result

            box = (wk_result or {}).get("box_view") or {}
            print(f"   (A) mMatchupScore: schedule内マッチアップ{box.get('schedule_matchup_count', 0)}件 "
                  f"/ サンプル{box.get('sample_count', 0)}選手")
            for s in box.get("samples", []):
                _print_raw_stat_entries("A:mMatchupScore", s["raw_player"])

            card = (wk_result or {}).get("playercard_view") or {}
            card_players = card.get("players", [])
            print(f"   (B) kona_playercard: {len(card_players)}選手")
            for p in card_players:
                _print_raw_stat_entries("B:kona_playercard", p)

        all_results["seasons"][str(year)] = season_result

    try:
        os.makedirs(os.path.dirname(PROJ_OUT_JSON), exist_ok=True)
        with open(PROJ_OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
        print(f"[OK] JSON保存: {PROJ_OUT_JSON}")
    except Exception as e:
        print(f"[NG] JSON保存失敗: {type(e).__name__}: {e}")
        print(traceback.format_exc())

    print("=" * 60)
    print("このスクリプトはここで判定を出さない。docs/data/probe_espn_proj.json を")
    print("git fetchしてサンドボックス側でFantasyProsの当時ECRと突き合わせること。")


if __name__ == "__main__":
    for year in (2024, 2025):
        probe(year)
    print("=" * 60)
    print("判定の見方: ドラフト/スタメンがOKなら→P3(GM傾向分析)で過去2年分を使える。")
    print("トランザクションがNGなら→2026年分の自動記録(v3.1.1で導入済み)が唯一のソース。")

    try:
        probe_projections()
    except Exception as e:
        print(f"[NG] probe_projections()全体で予期しない失敗: {type(e).__name__}: {e}")
        print(traceback.format_exc())
