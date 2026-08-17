# -*- coding: utf-8 -*-
"""P0: ESPN接続テスト
GitHub Actions上で実行し、リーグデータが取得できるかを確認する。
認証情報は環境変数(GitHub Secrets)から読む。コードには一切書かない。
"""
import os
import sys

from espn_api.football import League

def normalize_swid(swid: str) -> str:
    swid = swid.strip()
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return swid.upper().replace("{", "{").replace("}", "}")

def main():
    league_id = int(os.environ["LEAGUE_ID"].strip())
    espn_s2 = os.environ["ESPN_S2"].strip()
    swid = normalize_swid(os.environ["SWID"])

    print("=" * 60)
    print("P0 ESPN接続テスト 結果")
    print("=" * 60)

    ok_years = []
    ng_years = []

    for year in [2026, 2025, 2024, 2023]:
        print(f"\n----- シーズン {year} -----")
        try:
            lg = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
            print(f"[OK] リーグ名: {lg.settings.name}")
            print(f"     チーム数: {len(lg.teams)}")
            for t in lg.teams:
                owner = ""
                if t.owners and isinstance(t.owners, list) and isinstance(t.owners[0], dict):
                    owner = t.owners[0].get("firstName", "") or ""
                print(f"      - team_id={t.team_id} | {t.team_name} | owner: {owner} | "
                      f"{t.wins}勝{t.losses}敗")
            draft = getattr(lg, "draft", None) or []
            print(f"     ドラフトピック数: {len(draft)}")
            if draft:
                first = draft[0]
                print(f"      1巡1位: {first.playerName} (by team_id={first.team.team_id})")
            # 自チーム(team_id=1)のロスター
            my = next((t for t in lg.teams if t.team_id == 1), None)
            if my:
                print(f"     自チーム [{my.team_name}] ロスター({len(my.roster)}名):")
                for p in my.roster[:20]:
                    print(f"      - {p.name} ({p.position}, {p.proTeam})")
            ok_years.append(year)
        except Exception as e:
            print(f"[NG] {year}: {type(e).__name__}: {e}")
            ng_years.append(year)

    print("\n" + "=" * 60)
    print(f"取得成功: {ok_years}")
    print(f"取得失敗: {ng_years}")
    print("=" * 60)

    if not ok_years:
        print("\nすべての年で失敗しました。クッキー(ESPN_S2/SWID)の値を確認してください。")
        sys.exit(1)

if __name__ == "__main__":
    main()
