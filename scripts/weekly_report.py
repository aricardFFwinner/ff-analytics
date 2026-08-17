# -*- coding: utf-8 -*-
"""週次レポート生成のエントリポイント。

環境変数:
  LEAGUE_ID / ESPN_S2 / SWID : ESPN認証(必須)
  GEMINI_API_KEY             : 任意。あればAIコメントを埋め込む
  MOCK_SNAPSHOT              : テスト用。JSONパスを指定するとESPNを呼ばない
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ff_config import SEASON
import analysis
import report_html

JST = timezone(timedelta(hours=9))
DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")


def load_snapshot():
    mock = os.environ.get("MOCK_SNAPSHOT")
    if mock:
        with open(mock, encoding="utf-8") as f:
            return json.load(f), " (モックデータ)"
    import espn_snapshot
    return espn_snapshot.fetch_snapshot(), ""


def enrich_with_nfl_schedule(snapshot, week):
    """nflverse日程+天候+オッズをプレイヤーにマージ。失敗しても続行。"""
    try:
        import nfl_schedule
        info = nfl_schedule.fetch_schedule_info()
    except Exception as e:
        print(f"[warn] nflverse日程の取得に失敗(続行): {e}")
        return

    week_gamedays = {g["gameday"]
                     for ti in info.values()
                     for wk, g in ti.get("games", {}).items()
                     if wk == week and g.get("gameday")}
    try:
        import odds_lines
        implied = odds_lines.fetch_implied_totals(week_gamedays)
    except Exception as e:
        print(f"[warn] オッズ処理に失敗(続行): {e}")
        implied = {}
    snapshot["has_odds"] = bool(implied)

    try:
        import stadium_weather
    except Exception:
        stadium_weather = None

    # ルーキー判定(entry_year==当年)と、DST用の相手オフェンス指標
    rookies = set()
    try:
        import nfl_extra
        rookies = nfl_extra.fetch_rookies(SEASON)
        print(f"[info] ルーキー判定: {len(rookies)}名(entry_year={SEASON})")
    except Exception as e:
        print(f"[warn] ルーキーデータ取得失敗(続行): {e}")
        nfl_extra = None

    off_metrics, metrics_season = {}, None
    if nfl_extra:
        for season_try in (SEASON, SEASON - 1):
            try:
                m = nfl_extra.fetch_offense_metrics(season_try)
                games_max = max((v["games"] for v in m.values()), default=0)
                if m and (season_try < SEASON or games_max >= 3):
                    off_metrics, metrics_season = m, season_try
                    break
            except Exception as e:
                print(f"[warn] {season_try}オフェンス指標取得失敗: {e}")
        if metrics_season:
            print(f"[info] DST用オフェンス指標: {metrics_season}シーズン実績を使用")
    snapshot["dst_metrics_season"] = metrics_season

    def apply(p, fetch_weather=False):
        if nfl_extra and p.get("position") != "D/ST":
            p["is_rookie"] = nfl_extra.norm_name(p.get("name", "")) in rookies
        ab = nfl_schedule.to_nflverse_abbrev(p.get("pro_team", ""))
        ti = info.get(ab)
        if not ti:
            return
        p["bye"] = ti.get("bye")
        p["nfl_games"] = ti.get("games", {})
        g = ti["games"].get(week)
        if not g:
            if ti.get("bye") == week:
                p["this_week_opp"] = "BYE"
            return
        p["this_week_opp"] = ("vs " if g["is_home"] else "@ ") + g["opponent"]

        if stadium_weather:
            p["kickoff_jst"] = stadium_weather.kickoff_jst_label(g["gameday"], g["gametime"])
            roof_l = (g.get("roof") or "").lower()
            venue = "ドーム" if roof_l in ("dome", "closed") else "屋外"
            surface = (g.get("surface") or "").lower()
            if surface and surface != "grass":
                venue += "/人工芝"
            elif surface == "grass":
                venue += "/天然芝"
            p["venue_str"] = venue
            if fetch_weather:
                w, warn = stadium_weather.weather_for_game(
                    g.get("home_team", ""), g["gameday"], g["gametime"],
                    g.get("roof", ""), g.get("location", "") == "Neutral")
                if w and w not in ("ドーム",):
                    p["weather_str"] = w
                    p["wind_warn"] = warn

        if p.get("position") == "D/ST" and off_metrics:
            opp_ab_m = nfl_schedule.to_nflverse_abbrev(g["opponent"])
            om = off_metrics.get(opp_ab_m)
            if om:
                p["dst_opp_metrics"] = {**om, "season": metrics_season}

        team_line = implied.get(ab)
        if team_line:
            p["implied_total"] = team_line["implied"]
            p["game_ou"] = team_line["ou"]
            opp_ab = nfl_schedule.to_nflverse_abbrev(g["opponent"])
            opp_line = implied.get(opp_ab)
            if opp_line:
                p["opp_implied"] = opp_line["implied"]

    for t in snapshot["teams"]:
        is_mine = t["team_id"] == snapshot["my_team_id"]
        for p in t["roster"]:
            apply(p, fetch_weather=is_mine)  # 天候APIは自ロスター分に限定
    for pos_players in (snapshot.get("free_agents") or {}).values():
        for p in pos_players:
            apply(p, fetch_weather=False)


def maybe_gemini_comment(ai_summary):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    try:
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        prompt = (
            "あなたはファンタジーフットボールの専門アナリスト。以下の週次データを読み、"
            "(1)スタメンの妥当性 (2)FA獲得の優先順位 (3)今週特に注意すべき点、を"
            "日本語で簡潔に(箇条書き5行以内で)助言して。\n\n" + ai_summary
        )
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[warn] Geminiコメント生成に失敗(スキップ): {e}")
        return None


def main():
    snapshot, mode_note = load_snapshot()
    week = int(snapshot.get("current_week") or 1)
    enrich_with_nfl_schedule(snapshot, week)

    my_team = next(t for t in snapshot["teams"] if t["team_id"] == snapshot["my_team_id"])
    starters, bench, close_calls = analysis.pick_lineup(my_team["roster"], week)
    recs, drop_candidates = analysis.fa_recommendations(snapshot, week)
    rookie_info = analysis.rookie_swap(snapshot, week)
    byes = analysis.bye_overview(my_team["roster"])
    champ = analysis.championship_opponents(my_team["roster"])
    league = analysis.league_table(snapshot)
    ai_summary = analysis.build_ai_summary(
        snapshot, week, starters, bench, close_calls, recs, drop_candidates,
        rookie_info=rookie_info)
    ai_comment = maybe_gemini_comment(ai_summary)

    now = datetime.now(JST)
    ctx = {
        "league_name": snapshot["league_name"],
        "my_team_name": my_team["name"],
        "week": week,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "mode_note": mode_note,
        "starters": starters, "bench": bench, "close_calls": close_calls,
        "recs": recs, "drop_candidates": drop_candidates,
        "byes": byes, "champ": champ, "league": league,
        "ai_summary": ai_summary, "ai_comment": ai_comment,
        "has_odds": snapshot.get("has_odds", False),
        "rookie_info": rookie_info,
    }
    html_text = report_html.render(ctx)

    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(os.path.join(DOCS, "archive"), exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_text)
    stamp = now.strftime("%Y%m%d_%H%M")
    with open(os.path.join(DOCS, "archive", f"week{week}_{stamp}.html"), "w", encoding="utf-8") as f:
        f.write(html_text)
    with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    # Actionsログ用サマリー
    print("=" * 50)
    print(f"レポート生成完了: {snapshot['league_name']} Week {week}{mode_note}")
    print(f"スタメン: " + ", ".join(f'{s["slot"]}:{s["name"]}({s["score"]})' for s in starters))
    if close_calls:
        print("僅差: " + " / ".join(f'{c["slot"]} {c["starter"]} vs {c["rival"]}' for c in close_calls))
    print("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
