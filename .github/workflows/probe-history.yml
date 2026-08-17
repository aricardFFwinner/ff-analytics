# -*- coding: utf-8 -*-
"""過去シーズン(2024/2025)の遡り可否チェック(手動実行用)。

確認対象:
  1. ドラフトログ(League.draft)
  2. トランザクション履歴(League.recent_activity)
  3. 各週のスタメン/ベンチ(League.box_scores)
結果はActionsのログに出力するだけ(ファイルは書かない)。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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


if __name__ == "__main__":
    for year in (2024, 2025):
        probe(year)
    print("=" * 60)
    print("判定の見方: ドラフト/スタメンがOKなら→P3(GM傾向分析)で過去2年分を使える。")
    print("トランザクションがNGなら→2026年分の自動記録(v3.1.1で導入済み)が唯一のソース。")
