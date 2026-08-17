# -*- coding: utf-8 -*-
"""HTMLレポート生成。外部依存なしの自己完結ページを書き出す。"""
import html


CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1a202c; --sub:#64748b; --line:#e2e8f0;
        --accent:#2563eb; --good:#16a34a; --warn:#d97706; --bad:#dc2626; }
* { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,'Hiragino Sans','Noto Sans JP',sans-serif;
       background:var(--bg); color:var(--ink); line-height:1.55; }
.wrap { max-width:900px; margin:0 auto; padding:16px; }
h1 { font-size:1.25rem; margin:.2rem 0; }
h2 { font-size:1.05rem; margin:1.6rem 0 .5rem; border-left:4px solid var(--accent); padding-left:.5rem; }
.sub { color:var(--sub); font-size:.8rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px; margin:10px 0; overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.85rem; }
th { text-align:left; color:var(--sub); font-weight:600; border-bottom:2px solid var(--line);
     padding:6px 8px; white-space:nowrap; }
td { border-bottom:1px solid var(--line); padding:6px 8px; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.tag { display:inline-block; font-size:.7rem; padding:1px 7px; border-radius:99px;
       background:#eef2ff; color:var(--accent); font-weight:600; }
.tag.warn { background:#fef3c7; color:var(--warn); }
.tag.bad { background:#fee2e2; color:var(--bad); }
.tag.good { background:#dcfce7; color:var(--good); }
.me { background:#eff6ff; }
.gain-pos { color:var(--good); font-weight:600; }
textarea { width:100%; height:280px; font-size:.75rem; font-family:ui-monospace,monospace;
           border:1px solid var(--line); border-radius:8px; padding:8px; }
button.copy { background:var(--accent); color:#fff; border:none; border-radius:8px;
              padding:8px 16px; font-size:.85rem; cursor:pointer; margin-top:6px; }
.note { font-size:.78rem; color:var(--sub); margin-top:4px; }
"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


def _inj_tag(status):
    s = (status or "").upper()
    if not s or s == "ACTIVE":
        return ""
    cls = "bad" if s in ("OUT", "INJURY_RESERVE", "SUSPENSION") else "warn"
    return f' <span class="tag {cls}">{esc(s)}</span>'


def render(ctx):
    """ctx: weekly_report.py が組み立てる辞書。"""
    s = []
    s.append(f"<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>")
    s.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    s.append(f"<title>{esc(ctx['league_name'])} 週次レポート</title><style>{CSS}</style></head><body><div class='wrap'>")
    s.append(f"<h1>🏈 {esc(ctx['league_name'])} — Week {ctx['week']} レポート</h1>")
    sources = "ESPN予測 + NFL日程 + 天候(Open-Meteo)"
    if ctx.get("has_odds"):
        sources += " + ブックメーカーライン(The Odds API)"
    if (ctx.get("opp_info") or {}).get("available"):
        sources += " + 機会指標(nflverse)"
    s.append(f"<div class='sub'>生成: {esc(ctx['generated_at'])} (日本時間) / チーム: {esc(ctx['my_team_name'])} / "
             f"データ: {sources}{esc(ctx.get('mode_note',''))} / "
             f"<a href='dashboard.html'>📊 リーグダッシュボード →</a></div>")

    # 推奨スタメン
    has_odds = ctx.get("has_odds")
    tot_th = "<th class='num'>自軍Tot</th>" if has_odds else ""
    s.append("<h2>推奨スタメン</h2><div class='card'><table>")
    s.append(f"<tr><th>枠</th><th>選手</th><th>Pos/Team</th><th>今週の相手</th><th>キックオフ(日本)</th>"
             f"<th>会場/天候</th>{tot_th}<th class='num'>予測pt</th></tr>")

    def _name_cell(p):
        r = " <span class='tag'>R</span>" if p.get("is_rookie") else ""
        return f"{esc(p['name'])}{r}{_inj_tag(p.get('injury_status'))}"

    def _opp_cell(p):
        v = esc(p.get("this_week_opp", ""))
        m = p.get("dst_opp_metrics")
        if m:
            v += (f"<br><span class='sub'>被Sk{m['sk_g']}/G・TO{m['to_g']}/G"
                  f" ({m['season']}実績)</span>")
        return v

    def _venue_cell(p):
        v = esc(p.get("venue_str", ""))
        w = p.get("weather_str", "")
        if w:
            warn = " <span class='tag warn'>強風</span>" if p.get("wind_warn") else ""
            v += f"<br><span class='sub'>{esc(w)}</span>{warn}"
        return v

    def _tot_cell(p):
        if not has_odds:
            return ""
        if p.get("position") == "D/ST":
            # DSTは相手オフェンスの予想得点が低いほど良い(表示・色を反転)
            it = p.get("opp_implied")
            if it is None:
                return "<td class='num'>-</td>"
            hi = " style='color:var(--good);font-weight:600'" if it <= 18 else (
                 " style='color:var(--bad)'" if it >= 26 else "")
            return f"<td class='num'{hi}>相手{it}</td>"
        it = p.get("implied_total")
        if it is None:
            return "<td class='num'>-</td>"
        hi = " style='color:var(--good);font-weight:600'" if it >= 26 else (
             " style='color:var(--bad)'" if it <= 18 else "")
        return f"<td class='num'{hi}>{it}</td>"

    for p in ctx["starters"]:
        s.append(f"<tr><td><b>{esc(p['slot'])}</b></td><td>{_name_cell(p)}</td>"
                 f"<td>{esc(p['position'])}/{esc(p['pro_team'])}</td><td>{_opp_cell(p)}</td>"
                 f"<td>{esc(p.get('kickoff_jst',''))}</td><td>{_venue_cell(p)}</td>{_tot_cell(p)}"
                 f"<td class='num'><b>{p['score']}</b></td></tr>")
    s.append("</table>")
    s.append(f"<table style='margin-top:10px'><tr><th>ベンチ</th><th>Pos/Team</th><th>今週の相手</th>"
             f"<th>キックオフ(日本)</th><th>会場/天候</th>{tot_th}<th class='num'>予測pt</th></tr>")
    for p in ctx["bench"]:
        s.append(f"<tr><td>{_name_cell(p)}</td>"
                 f"<td>{esc(p['position'])}/{esc(p['pro_team'])}</td><td>{_opp_cell(p)}</td>"
                 f"<td>{esc(p.get('kickoff_jst',''))}</td><td>{_venue_cell(p)}</td>{_tot_cell(p)}"
                 f"<td class='num'>{p['score']}</td></tr>")
    s.append("</table>")
    if has_odds:
        s.append("<div class='note'>「自軍Tot」= ブックメーカーが予想するその選手の所属チームの得点(インプライドトータル)。"
                 "26点以上は攻撃が期待できる試合(緑)、18点以下はロースコア警戒(赤)。"
                 "<b>D/STのみ「相手Tot」=相手オフェンスの予想得点</b>で、低いほど守備の得点機会が多い(18以下=緑が狙い目、26以上=赤は危険)。</div>")
    if ctx["close_calls"]:
        s.append("<div class='note'>⚖️ <b>僅差の枠</b>(AIに相談する価値あり): " + " / ".join(
            f"{esc(c['slot'])}: {esc(c['starter'])}({c['starter_score']}) vs {esc(c['rival'])}({c['rival_score']})"
            for c in ctx["close_calls"]) + "</div>")
    s.append("</div>")

    # P1.5: 機会指標アラート
    oi = ctx.get("opp_info") or {}
    if oi.get("available"):
        early = oi["mode"] == "early"
        mode_tag = ("<span class='tag warn'>アーリーモード(単週判定)</span>" if early
                    else "<span class='tag good'>通常モード</span>")
        s.append(f"<h2>機会指標アラート <span class='sub'>(nflverse実データ W{oi['last_week']}時点)</span></h2>")
        s.append("<div class='card'>")
        s.append(f"<div class='note'>{mode_tag} 🔥=機会はスタメン級・得点まだ(ブレイク前夜、即獲得検討) "
                 "👀=その一歩手前 💎=得点も機会も実証済みなのにFA放置 ⚠️=自軍で機会減少中(売り時)。"
                 "「E」付きは単週データ判定(確度低め。ただし機会は嘘をつきにくい)。</div>")
        if oi.get("small_sample"):
            s.append("<div class='note'>⚠ データが2週以下のため小サンプル。閾値は意図的に「速度優先」設定"
                     "(6人リーグは誤拾いコスト≈0、見逃しコスト大)。</div>")

        if oi.get("swap_pairs"):
            s.append("<table><tr><th>入れ替え案</th><th>獲得</th><th>放出</th>"
                     "<th class='num'>ネット改善</th></tr>")
            for i, sp in enumerate(oi["swap_pairs"][:5], 1):
                a, d = sp["add"], sp["drop"]
                net_cls = "gain-pos" if sp["net"] > 0 else ""
                s.append(f"<tr><td><b>案{i}</b></td>"
                         f"<td>{esc(a['opp_tag'])} <b>{esc(a['name'])}</b> ({esc(a['position'])}/{esc(a['pro_team'])})"
                         f"<br><span class='sub'>{sp['add_value']}pt/G ({esc(sp['add_basis'])})</span></td>"
                         f"<td>{esc(d['name'])} ({esc(d['position'])})"
                         f"<br><span class='sub'>{sp['drop_value']}pt/G ({esc(sp['drop_basis'])}) {esc(sp['drop_zone_note'])}</span></td>"
                         f"<td class='num'><span class='{net_cls}'>{sp['net']:+}</span></td></tr>")
            s.append("</table>")

        if oi.get("fa_tagged"):
            s.append("<table style='margin-top:10px'><tr><th></th><th>FA選手</th><th>Pos/Team</th>"
                     "<th class='num'>Snap%</th><th class='num'>TS%/加重機会</th><th class='num'>WOPR</th>"
                     "<th class='num'>RZ(2週)</th><th class='num'>直近pt/G</th><th class='num'>xFP/G</th><th>傾向</th></tr>")
            for p in oi["fa_tagged"][:12]:
                o = p.get("opp") or {}
                snap = f"{round(o['snap']*100)}" if o.get("snap") is not None else "-"
                if p["position"] == "RB":
                    usage = f"{round(o['wtd_avg'],1)}" if o.get("wtd_avg") is not None else "-"
                else:
                    usage = f"{round(o['ts_avg']*100)}%" if o.get("ts_avg") is not None else "-"
                wopr = f"{o['wopr_avg']:.2f}" if o.get("wopr_avg") is not None else "-"
                ppg = o.get("ppg3", "-")
                xfp = o.get("xfp3", "-") if o.get("xfp3") is not None else "-"
                s.append(f"<tr><td>{esc(p['opp_tag'])}</td><td><b>{esc(p['name'])}</b>{_inj_tag(p.get('injury_status'))}</td>"
                         f"<td>{esc(p['position'])}/{esc(p['pro_team'])}</td>"
                         f"<td class='num'>{snap}</td><td class='num'>{usage}</td><td class='num'>{wopr}</td>"
                         f"<td class='num'>{o.get('rz2','-')}</td><td class='num'><b>{ppg}</b></td>"
                         f"<td class='num'>{xfp}</td><td>{esc(o.get('trend','-'))}</td></tr>")
            s.append("</table>")
            st = oi.get("startable") or {}
            s.append("<div class='note'>スタメン級ライン(直近実PPG基準): "
                     + " / ".join(f"{esc(k)} {v}pt" for k, v in st.items())
                     + "。xFP/G=機会から算出した期待FP(参考列)。直近pt/Gがこれより大きく下なら不運の可能性=買い。</div>")

        if oi.get("my_sell"):
            s.append("<table style='margin-top:10px'><tr><th>⚠️ 自軍の売り時シグナル</th><th>理由</th></tr>")
            for p in oi["my_sell"]:
                s.append(f"<tr><td><b>{esc(p['name'])}</b> ({esc(p['position'])}/{esc(p['pro_team'])})</td>"
                         f"<td>{esc(p.get('sell_reason',''))}</td></tr>")
            s.append("</table><div class='note'>ブローアウト週(21点差以上)は判定から除外済み。怪我による欠場明けは1週見てから判断。</div>")

        if not oi.get("fa_tagged") and not oi.get("my_sell") and not oi.get("swap_pairs"):
            s.append("<div class='note'>今週のアラートなし(タグ条件を満たす選手がいません)。</div>")
        s.append("</div>")

    # FA推奨
    s.append("<h2>FA / Waiver おすすめ</h2>")
    for pos, r in ctx["recs"].items():
        if not r["fa"]:
            continue
        my_worst = r.get("my_worst")
        worst_note = f"あなたの同ポジ最弱: {esc(my_worst[0])} {my_worst[1]}pt" if my_worst else "同ポジの保有なし"
        fa_tot_th = "<th class='num'>自軍Tot</th>" if has_odds else ""
        s.append(f"<div class='card'><b>{esc(pos)}</b> <span class='sub'>({worst_note})</span><table>")
        opp_th = ("<th class='num'>Snap%</th><th class='num'>直近pt/G</th><th>傾向</th>"
                  if (ctx.get("opp_info") or {}).get("available") and pos not in ("D/ST", "K") else "")
        s.append(f"<tr><th>選手</th><th>Team</th><th>今週の相手</th>{fa_tot_th}<th class='num'>予測pt</th>"
                 f"<th class='num'>最弱比</th>{opp_th}<th class='num'>own%</th></tr>")
        for x in r["fa"]:
            gain = x.get("gain_vs_my_worst")
            gain_html = f"<span class='gain-pos'>+{gain}</span>" if (gain is not None and gain > 0) else (esc(gain) if gain is not None else "-")
            opp_td = ""
            if opp_th:
                o = x.get("opp") or {}
                snap = f"{round(o['snap']*100)}" if o.get("snap") is not None else "-"
                ppg = o.get("ppg3", "-") if o.get("ppg3") is not None else "-"
                tag = f" {esc(x['opp_tag'])}" if x.get("opp_tag") else ""
                opp_td = (f"<td class='num'>{snap}</td><td class='num'>{ppg}</td>"
                          f"<td>{esc(o.get('trend','-'))}{tag}</td>")
            s.append(f"<tr><td>{_name_cell(x)}</td><td>{esc(x['pro_team'])}</td>"
                     f"<td>{_opp_cell(x)}</td>{_tot_cell(x)}<td class='num'>{x['score']}</td>"
                     f"<td class='num'>{gain_html}</td>{opp_td}<td class='num'>{esc(x.get('percent_owned',''))}</td></tr>")
        s.append("</table></div>")
    if ctx["drop_candidates"]:
        s.append("<div class='note'>ドロップ候補(低予測順、\"R\"=ルーキー、✅=期待も機会も低く安全 / 🟡=期待は高いが機会低下・1週様子見推奨): " + ", ".join(
            f"{esc(z)}{esc(n)}({esc(p)}) {sc}pt" for n, p, sc, z in ctx["drop_candidates"]) + "</div>")

    # ルーキールール(Week5まで)
    ri = ctx.get("rookie_info")
    if ri:
        ok = ri["my_count"] >= ri["min_count"]
        status = ("<span class='tag good'>ルール充足</span>" if ok
                  else "<span class='tag bad'>ルール違反状態!</span>")
        s.append(f"<h2>ルーキー保有ルール(Week{ri['until_week']}まで)</h2><div class='card'>")
        s.append(f"<div class='note'>NFL1年目の選手を常に{ri['min_count']}人以上保持する義務。現在{ri['my_count']}人 {status}"
                 f"<br>ルーキーを切る場合は、必ず別のルーキー獲得と同時に行うこと。</div>")
        s.append("<table><tr><th>保有中のルーキー</th><th>Pos/Team</th><th class='num'>予測pt</th></tr>")
        for p in ri["my_rookies"]:
            s.append(f"<tr><td>{_name_cell(p)}</td><td>{esc(p['position'])}/{esc(p['pro_team'])}</td>"
                     f"<td class='num'>{p['score']}</td></tr>")
        s.append("</table>")
        if ri["fa_rookies"]:
            s.append("<table style='margin-top:10px'><tr><th>FAで獲れるルーキー上位</th><th>Pos/Team</th>"
                     "<th>今週の相手</th><th class='num'>予測pt</th><th class='num'>最弱R比</th><th class='num'>own%</th></tr>")
            for p in ri["fa_rookies"]:
                g = p.get("gain_vs_my_worst_rookie")
                gh = f"<span class='gain-pos'>+{g}</span>" if (g is not None and g > 0) else (esc(g) if g is not None else "-")
                s.append(f"<tr><td>{_name_cell(p)}</td><td>{esc(p['position'])}/{esc(p['pro_team'])}</td>"
                         f"<td>{esc(p.get('this_week_opp',''))}</td><td class='num'>{p['score']}</td>"
                         f"<td class='num'>{gh}</td><td class='num'>{esc(p.get('percent_owned',''))}</td></tr>")
            s.append("</table>")
            s.append("<div class='note'>「最弱R比」= あなたの保有ルーキーで最も予測ptが低い選手との差。プラスなら入れ替え候補。</div>")
        s.append("</div>")

    # Bye週
    s.append("<h2>Bye週マップ(自ロスター)</h2><div class='card'><table><tr><th>Week</th><th>Bye選手</th></tr>")
    for wk, names in ctx["byes"].items():
        warn = " <span class='tag warn'>要注意</span>" if len(names) >= 3 else ""
        s.append(f"<tr><td>W{wk}{warn}</td><td>{esc(', '.join(names))}</td></tr>")
    s.append("</table></div>")

    # チャンピオンシップ週
    s.append("<h2>チャンピオンシップ週(W15-17)の対戦先読み</h2><div class='card'>"
             "<div class='note'>優勝はこの3週で決まる。トレード・FA判断はここに強い選手を優先。</div><table>")
    s.append("<tr><th>選手</th><th>Pos</th><th>W15</th><th>W16</th><th>W17</th></tr>")
    for row in ctx["champ"]:
        cells = "".join(f"<td>{esc(w.split(': ',1)[1] if ': ' in w else w)}</td>" for w in row["weeks"])
        s.append(f"<tr><td>{esc(row['name'])}</td><td>{esc(row['position'])}</td>{cells}</tr>")
    s.append("</table></div>")

    # リーグ順位
    s.append("<h2>リーグ状況</h2><div class='card'><table>")
    s.append("<tr><th>#</th><th>チーム</th><th>オーナー</th><th>成績</th><th class='num'>得点</th>"
             "<th class='num'>失点</th><th class='num'>Waiver順</th><th class='num'>PO確率%</th></tr>")
    for t in ctx["league"]:
        cls = " class='me'" if t["is_me"] else ""
        s.append(f"<tr{cls}><td>{t['standing']}</td><td>{esc(t['name'])}</td><td>{esc(t['owner'])}</td>"
                 f"<td>{esc(t['record'])}</td><td class='num'>{t['pf']}</td><td class='num'>{t['pa']}</td>"
                 f"<td class='num'>{t['waiver']}</td><td class='num'>{t['playoff_pct']}</td></tr>")
    s.append("</table></div>")

    # AIコメント(任意)
    if ctx.get("ai_comment"):
        s.append("<h2>AIコメント(Gemini自動生成)</h2><div class='card'>")
        s.append("".join(f"<p>{esc(line)}</p>" for line in ctx["ai_comment"].split("\n") if line.strip()))
        s.append("</div>")

    # AI相談用サマリー
    s.append("<h2>AI相談用サマリー(コピーして無料AIに貼る)</h2><div class='card'>")
    s.append(f"<textarea id='aisum' readonly>{esc(ctx['ai_summary'])}</textarea>")
    s.append("<br><button class='copy' onclick=\"navigator.clipboard.writeText(document.getElementById('aisum').value).then(()=>this.textContent='コピーしました!')\">サマリーをコピー</button>")
    s.append("</div>")

    s.append(f"<div class='sub' style='margin:20px 0'>過去レポート: <a href='./archive/'>archive/</a></div>")
    s.append("</div></body></html>")
    return "".join(s)
