# -*- coding: utf-8 -*-
"""P2: dashboard.html の生成(自己完結・外部依存なし)。

チャート設計はdatavizの手法に従う:
- 6チームの系列色は検証済みカテゴリカルパレット(チームIDに固定割当、順位で塗り替えない)
- 推移グラフ: 2px線・ホバー(クロスヘア+ツールチップ)・凡例+末尾直接ラベル・テーブルビュー併設
- ヒートマップ: 平均比の極性 → ダイバージング(青=強い↔赤=弱い、中立グレー midpoint)
- ステータス色は系列色と混用しない
"""
import html
import json

# 検証済みカテゴリカルパレット(light、白サーフェスで6スロット全チェック合格)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASE = "#c3c2b7"
NEUTRAL = "#f0efec"

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
.me { background:#eff6ff; }
.tag { display:inline-block; font-size:.7rem; padding:1px 7px; border-radius:99px;
       background:#eef2ff; color:var(--accent); font-weight:600; }
.tag.warn { background:#fef3c7; color:var(--warn); }
.banner { background:linear-gradient(135deg,#1e3a8a,#2563eb); color:#fff; border-radius:12px;
          padding:14px 16px; margin:12px 0; }
.banner .big { font-size:1.5rem; font-weight:700; }
.banner .line { font-size:.9rem; margin-top:2px; opacity:.95; }
.delta-up { color:#a7f3d0; } .delta-down { color:#fecaca; }
.pbar { height:8px; border-radius:4px; background:#e2e8f0; min-width:60px; position:relative; }
.pbar > div { height:8px; border-radius:4px; background:#2a78d6; }
.legend { display:flex; flex-wrap:wrap; gap:10px; font-size:.78rem; color:#52514e; margin:6px 0; }
.legend .sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
.note { font-size:.78rem; color:var(--sub); margin-top:4px; }
.weeknav { font-size:.8rem; margin:4px 0; }
.weeknav a { margin-right:6px; }
details summary { cursor:pointer; color:var(--sub); font-size:.8rem; margin-top:6px; }
#tt { position:absolute; display:none; background:#fff; border:1px solid #e2e8f0; border-radius:8px;
      padding:6px 10px; font-size:.75rem; box-shadow:0 2px 8px rgba(0,0,0,.12); pointer-events:none; z-index:9; }
"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


def _dhv(p):
    """表示用の選手見込みpt/G(ブレンド優先)。"""
    v = p.get("blend_ppg")
    if v is not None:
        return float(v)
    return float(p.get("proj_avg") or 0.0)


def team_color(team_ids_sorted, tid):
    """系列色はteam_idに固定割当(順位や表示順で塗り替えない)。"""
    return SERIES[team_ids_sorted.index(tid) % len(SERIES)]


def _heat_cell(val, avg, span):
    """平均比のダイバージング背景(青=強い/グレー=平均/赤=弱い)。文字は常にインク色。"""
    if avg is None or span <= 0:
        return f"<td class='num'>{val}</td>"
    d = (val - avg) / span  # -1..1に正規化
    d = max(-1.0, min(1.0, d))
    if abs(d) < 0.12:
        bg = NEUTRAL
    elif d > 0:
        bg = f"rgba(42,120,214,{0.12 + 0.38 * d:.2f})"
    else:
        bg = f"rgba(227,73,72,{0.12 + 0.38 * (-d):.2f})"
    return f"<td class='num' style='background:{bg};color:{INK}'>{val}</td>"


def _trend_svg(history, teams, my_id, current_week):
    """優勝確率の推移(6系列ライン、SVG+ホバー)。データ1週分でも描く。"""
    weeks = sorted(int(w) for w in history.keys())
    if not weeks:
        return "", ""
    tids = sorted(t["team_id"] for t in teams)
    names = {t["team_id"]: (t.get("owner") or t["name"]) for t in teams}
    W, H, L, R, T, B = 860, 300, 52, 110, 16, 36
    x0, x1 = min(weeks), max(max(weeks), min(weeks) + 1)

    # Y軸上限はデータに合わせて動的化(全チーム低確率の序盤に下部へ潰れないように)
    peak = max((e.get("champ_pct", 0) or 0)
               for w in history.values() for e in w.get("teams", {}).values())
    ymax = min(100, max(25, int((peak // 25 + 1) * 25)))

    def X(w):
        return L + (W - L - R) * (w - x0) / (x1 - x0)

    def Y(v):
        return T + (H - T - B) * (1 - v / float(ymax))

    s = [f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto' role='img' "
         f"aria-label='優勝確率の推移'>"]
    for gv in range(0, ymax + 1, max(ymax // 5, 5)):
        y = Y(gv)
        s.append(f"<line x1='{L}' y1='{y:.1f}' x2='{W-R}' y2='{y:.1f}' stroke='{GRID}' stroke-width='1'/>")
        s.append(f"<text x='{L-6}' y='{y+3:.1f}' text-anchor='end' font-size='14' fill='{MUTED}'>{gv}%</text>")
    for w in weeks:
        s.append(f"<text x='{X(w):.1f}' y='{H-B+14}' text-anchor='middle' font-size='14' fill='{MUTED}'>W{w}</text>")
    s.append(f"<line x1='{L}' y1='{Y(0):.1f}' x2='{W-R}' y2='{Y(0):.1f}' stroke='{BASE}' stroke-width='1'/>")

    series_pts = {}
    for tid in tids:
        pts = []
        for w in weeks:
            e = history.get(str(w), {}).get("teams", {}).get(str(tid))
            if e and e.get("champ_pct") is not None:
                pts.append((w, e["champ_pct"]))
        series_pts[tid] = pts
    # 直接ラベルは最終週上位3+自分(datavizの選択的ラベル。全点ラベルはしない)
    last_vals = {tid: pts[-1][1] for tid, pts in series_pts.items() if pts}
    label_ids = set(sorted(last_vals, key=lambda k: -last_vals[k])[:2]) | {my_id}
    used_y = []
    for tid in tids:
        pts = series_pts[tid]
        if not pts:
            continue
        c = team_color(tids, tid)
        wgt = 3 if tid == my_id else 2
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(w):.1f},{Y(v):.1f}" for i, (w, v) in enumerate(pts))
        s.append(f"<path d='{path}' fill='none' stroke='{c}' stroke-width='{wgt}' stroke-linejoin='round'/>")
        for w, v in pts:
            s.append(f"<circle cx='{X(w):.1f}' cy='{Y(v):.1f}' r='3.5' fill='{c}' stroke='#fff' stroke-width='2'/>")
        if tid in label_ids:
            lw, lv = pts[-1]
            ly = Y(lv)
            while any(abs(ly - u) < 17 for u in used_y):
                ly += 17
            used_y.append(ly)
            s.append(f"<text x='{X(lw)+8:.1f}' y='{ly+3:.1f}' font-size='14' font-weight='600' fill='{INK2}'>"
                     f"{esc(names[tid])} {lv}%</text>")
    s.append("</svg>")

    data_js = json.dumps({
        "weeks": weeks,
        "series": [{"tid": tid, "name": names[tid], "color": team_color(tids, tid),
                    "pts": series_pts[tid]} for tid in tids],
        "geom": {"W": W, "H": H, "L": L, "R": R, "T": T, "B": B,
                 "x0": x0, "x1": x1},
    }, ensure_ascii=False)

    legend = "<div class='legend'>" + "".join(
        f"<span><span class='sw' style='background:{team_color(tids, tid)}'></span>{esc(names[tid])}</span>"
        for tid in tids) + "</div>"

    table = ["<details><summary>データをテーブルで見る</summary><table><tr><th>Week</th>"]
    table += [f"<th class='num'>{esc(names[tid])}</th>" for tid in tids]
    table.append("</tr>")
    for w in weeks:
        table.append(f"<tr><td>W{w}</td>")
        for tid in tids:
            e = history.get(str(w), {}).get("teams", {}).get(str(tid), {})
            table.append(f"<td class='num'>{e.get('champ_pct', '-')}</td>")
        table.append("</tr>")
    table.append("</table></details>")

    hover_js = """
<script>
(function(){
  var D = %DATA%;
  var svg = document.getElementById('trend');
  if (!svg) return;
  var tt = document.getElementById('tt');
  var g = D.geom;
  function wkFromX(px){
    var r = svg.getBoundingClientRect();
    var x = (px - r.left) / r.width * g.W;
    var f = (x - g.L) / (g.W - g.L - g.R) * (g.x1 - g.x0) + g.x0;
    var best = D.weeks[0], bd = 1e9;
    D.weeks.forEach(function(w){ var d = Math.abs(w - f); if (d < bd){bd = d; best = w;} });
    return best;
  }
  svg.addEventListener('mousemove', function(ev){
    var w = wkFromX(ev.clientX);
    var rows = D.series.map(function(sr){
      var p = sr.pts.filter(function(q){return q[0]===w;})[0];
      return p ? {name: sr.name, v: p[1], c: sr.color} : null;
    }).filter(Boolean).sort(function(a,b){return b.v-a.v;});
    if (!rows.length) return;
    tt.innerHTML = '<b>W' + w + '</b><br>' + rows.map(function(r){
      return '<span style="color:'+r.c+'">●</span> ' + r.name + ' ' + r.v + '%';
    }).join('<br>');
    tt.style.display = 'block';
    tt.style.left = (ev.pageX + 14) + 'px';
    tt.style.top = (ev.pageY + 10) + 'px';
  });
  svg.addEventListener('mouseleave', function(){ tt.style.display = 'none'; });
})();
</script>""".replace("%DATA%", data_js)

    svg_open = s[0].replace("<svg", "<svg id='trend'", 1)
    chart = ("<div style='position:relative'><div id='trendwrap'>"
             + svg_open + "".join(s[1:]) + "</div></div>"
             + legend + "".join(table))
    return chart, hover_js


def render(ctx):
    """ctx: dashboard.build_context()の出力。"""
    teams = ctx["teams"]
    my_id = ctx["my_team_id"]
    week = ctx["week"]
    sim = ctx.get("sim")
    tids = sorted(t["team_id"] for t in teams)
    names = {t["team_id"]: (t.get("owner") or t["name"]) for t in teams}

    s = ["<!DOCTYPE html><html lang='ja'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>{esc(ctx['league_name'])} ダッシュボード</title><style>{CSS}</style></head><body>",
         "<div id='tt'></div><div class='wrap'>",
         f"<h1>📊 {esc(ctx['league_name'])} — リーグダッシュボード <span class='sub'>W{week}時点</span></h1>",
         f"<div class='sub'>生成: {esc(ctx['generated_at'])} (日本時間) / "
         f"<a href='{esc(ctx['report_href'])}'>週次レポートへ →</a></div>"]

    if ctx.get("week_links"):
        s.append("<div class='weeknav'>過去週: " + " ".join(
            f"<a href='{esc(h)}'>W{w}</a>" for w, h in ctx["week_links"]) + "</div>")

    # ① 結論バナー
    if sim:
        me = sim["teams"][my_id]
        delta_html = ""
        if ctx.get("prev_champ") is not None:
            d = round(me["champ_pct"] - ctx["prev_champ"], 1)
            cls = "delta-up" if d >= 0 else "delta-down"
            delta_html = f" <span class='{cls}'>({'+' if d >= 0 else ''}{d}pt/先週比)</span>"
        threat = max((t for t in teams if t["team_id"] != my_id),
                     key=lambda t: sim["teams"][t["team_id"]]["champ_pct"])
        tm = sim["teams"][threat["team_id"]]
        all_in = sim.get("all_in_playoffs")
        po_label = (f"上位2シード(W15 Bye)獲得 {me.get('bye_pct', 0)}%" if all_in
                    else f"PO進出 {me['playoff_pct']}%")
        s.append("<div class='banner'>")
        s.append(f"<div class='big'>優勝確率 {me['champ_pct']}%{delta_html}</div>")
        s.append(f"<div class='line'>{po_label} / 最大の脅威: "
                 f"{esc(names[threat['team_id']])} (優勝{tm['champ_pct']}%)</div>")
        cond = sim.get("conditional")
        if cond:
            s.append(f"<div class='line'>今週勝てば {cond['win']}% ↔ 負ければ {cond['lose']}%</div>")
        top_trade = (ctx.get("trades") or [None])[0]
        if top_trade:
            g = "+".join(p["name"] for p in top_trade["give"])
            r = "+".join(p["name"] for p in top_trade["get"])
            s.append(f"<div class='line'>推奨アクション: {esc(names[top_trade['opp_team']['team_id']])}に"
                     f"トレード打診 ({esc(g)} ⇄ {esc(r)}) ↓詳細は候補一覧</div>")
        s.append("</div>")

    # ② 推移グラフ
    chart, hover_js = _trend_svg(ctx["history"], teams, my_id, week)
    if chart:
        s.append("<h2>優勝確率の推移</h2><div class='card'>")
        s.append(chart)
        s.append("</div>")

    # ③ 順位 + 確率
    all_in = (sim or {}).get("all_in_playoffs")
    po_col = "Bye獲得%" if all_in else "PO進出%"
    s.append("<h2>順位とシミュレーション</h2><div class='card'><table>")
    s.append(f"<tr><th>#</th><th>チーム</th><th>成績</th><th class='num'>PF</th>"
             f"<th class='num'>PA</th><th>{po_col}</th><th>優勝%</th><th class='num'>平均勝数</th></tr>")
    for t in sorted(teams, key=lambda t: (t.get("standing") or 99)):
        tid = t["team_id"]
        cls = " class='me'" if tid == my_id else ""
        m = (sim or {}).get("teams", {}).get(tid, {})
        po = m.get("bye_pct", 0) if all_in else m.get("playoff_pct", 0)
        ch = m.get("champ_pct", 0)
        sw = team_color(tids, tid)
        s.append(f"<tr{cls}><td>{t.get('standing', '')}</td>"
                 f"<td><span class='sw' style='display:inline-block;width:10px;height:10px;"
                 f"border-radius:2px;background:{sw};margin-right:5px'></span>{esc(names[tid])}"
                 f"<br><span class='sub'>{esc(t['name'])}</span></td>"
                 f"<td>{t.get('wins', 0)}-{t.get('losses', 0)}</td>"
                 f"<td class='num'>{t.get('points_for', 0)}</td><td class='num'>{t.get('points_against', 0)}</td>"
                 f"<td><div class='pbar'><div style='width:{po}%'></div></div>"
                 f"<span class='sub'>{po}%</span></td>"
                 f"<td><div class='pbar'><div style='width:{ch}%'></div></div>"
                 f"<span class='sub'>{ch}%</span></td>"
                 f"<td class='num'>{m.get('avg_wins', '-')}</td></tr>")
    s.append("</table>")
    if sim:
        s.append(f"<div class='note'>モンテカルロ{sim['sims']:,}回。前提: {esc(sim['assumption'])}。"
                 + ("全チームがW15-17に進出する形式のため「Bye獲得%」(レギュラー2位以内でW15を休める確率)を表示。" if all_in else "")
                 + ("W1-3は季節予測への依存が大きく確率は過信しないこと。" if week <= 3 else "")
                 + "</div>")
    s.append("</div>")

    # ④ 戦力ヒートマップ
    st = ctx["strength"]
    s.append("<h2>戦力ヒートマップ <span class='sub'>(スタメン見込みpt/G、リーグ平均比)</span></h2>")
    s.append("<div class='card'><table><tr><th>チーム</th>")
    for pos in st["positions"]:
        s.append(f"<th class='num'>{esc(pos)}</th>")
    s.append("<th class='num'>計</th></tr>")
    spans = {pos: max(abs(r["by_pos"][pos] - st["avg"][pos]) for r in st["rows"]) or 1
             for pos in st["positions"]}
    span_total = max(abs(r["total"] - st["avg"]["total"]) for r in st["rows"]) or 1
    for r in sorted(st["rows"], key=lambda r: -r["total"]):
        cls = " class='me'" if r["team_id"] == my_id else ""
        s.append(f"<tr{cls}><td>{esc(names[r['team_id']])}</td>")
        for pos in st["positions"]:
            s.append(_heat_cell(r["by_pos"][pos], st["avg"][pos], spans[pos]))
        s.append(_heat_cell(r["total"], st["avg"]["total"], span_total))
        s.append("</tr>")
    s.append("</table><div class='note'>青=リーグ平均より強い / 赤=弱い(色の濃さ=差の大きさ)。"
             "評価はブレンド値(ESPN予測×直近実績×機会指標。週が進むほど実績側に重心移動。開幕前はESPN予測のみ)。</div></div>")

    # ⑤ 余剰 / 不足
    sd = ctx["surplus"]
    s.append("<h2>余剰と不足 <span class='sub'>(トレードの当てどころ)</span></h2><div class='card'><table>")
    s.append("<tr><th>チーム</th><th>余剰(スタメン級の余り)</th><th>不足</th></tr>")
    for r in sd["rows"]:
        cls = " class='me'" if r["team_id"] == my_id else ""
        s.append(f"<tr{cls}><td>{esc(names[r['team_id']])}</td>"
                 f"<td>{esc(', '.join(r['surplus']) or '-')}</td>"
                 f"<td>{esc(', '.join(r['deficit']) or '-')}</td></tr>")
    s.append(f"</table><div class='note'>スタメン級ライン(ESPN予測pt/G): "
             + " / ".join(f"{k} {v}" for k, v in sd["lines"].items())
             + "。「余剰×相手の不足」が噛み合う相手がトレード候補(生成はP2次版)。</div></div>")

    # ⑦ W15-17 SoS
    sos = ctx.get("sos")
    if sos:
        s.append("<h2>チャンピオンシップ週(W15-17)のマッチアップ難易度</h2><div class='card'><table>")
        s.append("<tr><th>チーム</th>" + "".join(f"<th class='num'>{p}</th>" for p in ("QB", "RB", "WR", "TE")) + "</tr>")
        for r in sos["rows"]:
            cls = " class='me'" if r["team_id"] == my_id else ""
            s.append(f"<tr{cls}><td>{esc(names[r['team_id']])}</td>")
            for pos in ("QB", "RB", "WR", "TE"):
                v = r["by_pos"].get(pos)
                if v is None:
                    s.append("<td class='num'>-</td>")
                else:
                    mark = "🟢" if v <= 10 else ("🔴" if v >= 23 else "⚪")
                    s.append(f"<td class='num'>{mark} {v}</td>")
            s.append("</tr>")
        s.append(f"</table><div class='note'>数値=スタメンが対戦する守備の「被FPランク」平均"
                 f"(1-32、小さいほど楽な相手。{sos['season']}シーズン被FP実績ベース)。"
                 "🟢=1-10楽 / ⚪=11-22普通 / 🔴=23-32きつい。トレードで取る選手はここが🟢のチームの選手を優先。</div></div>")

    # ⑥ 競合カード(アコーディオン)
    cards = ctx.get("cards") or []
    if cards:
        s.append("<h2>競合チーム分析 <span class='sub'>(優勝確率順)</span></h2>")
        for c in cards:
            t = c["team"]
            m = c["sim"]
            trend_str = " → ".join(f"W{w}:{v}%" for w, v in c["trend"]) or "-"
            s.append(f"<div class='card'><details><summary><b>{esc(names[t['team_id']])}</b> "
                     f"({esc(t['name'])}) — 優勝{m.get('champ_pct', '-')}% / Bye{m.get('bye_pct', '-')}%"
                     f"</summary>")
            s.append("<table style='margin-top:8px'>")
            s.append(f"<tr><th>余剰</th><td>{esc(', '.join(c['surplus']) or '-')}</td></tr>")
            s.append(f"<tr><th>不足(=トレードの当てどころ)</th><td>{esc(', '.join(c['deficit']) or '-')}</td></tr>")
            if c["sos"]:
                sos_str = " / ".join(f"{pos} {v}" for pos, v in c["sos"].items())
                s.append(f"<tr><th>W15-17 SoS</th><td>{esc(sos_str)}</td></tr>")
            s.append(f"<tr><th>優勝確率の推移</th><td>{esc(trend_str)}</td></tr>")
            for w in c["watch"]:
                fa = w["fa"]
                d = (f" → 取られると相手の優勝確率{w['d_champ_pct']:+}pt"
                     if w.get("d_champ_pct") is not None else "")
                s.append(f"<tr><th>⚠️ブロック警戒FA</th><td>{esc(fa['name'])} "
                         f"({esc(fa['position'])}/{esc(fa['pro_team'])}) "
                         f"{round(_dhv(fa), 1)}pt/G{esc(d)}</td></tr>")
            s.append("</table></details></div>")

    # ⑧ トレード候補
    trades_list = ctx.get("trades") or []
    s.append("<h2>トレード候補 <span class='sub'>(自チーム視点、◎=win-win / ○=要交渉)</span></h2>")
    if trades_list:
        s.append("<div class='card'><table>")
        s.append("<tr><th></th><th>相手</th><th>出す</th><th>貰う</th>"
                 "<th class='num'>自分Δ<br>残り/W15-17</th><th class='num'>相手Δ<br>残り/W15-17</th>"
                 "<th class='num'>Δ優勝%</th><th>売り文句</th></tr>")
        for c in trades_list:
            give = "<br>".join(esc(p["name"]) + f" ({esc(p['position'])})" for p in c["give"])
            get = "<br>".join(esc(p["name"]) + f" ({esc(p['position'])})" for p in c["get"])
            dch = f"{c['d_champ_pct']:+}" if c.get("d_champ_pct") is not None else "-"
            s.append(f"<tr><td>{esc(c['grade'])}</td>"
                     f"<td>{esc(names[c['opp_team']['team_id']])}</td>"
                     f"<td>{give}</td><td>{get}</td>"
                     f"<td class='num'>{c['d_my_ros']:+} / {c['d_my_champ']:+}</td>"
                     f"<td class='num'>{c['d_opp_ros']:+} / {c['d_opp_champ']:+}</td>"
                     f"<td class='num'><b>{dch}</b></td>"
                     f"<td style='white-space:normal;min-width:10em'>{esc(c['pitch'])}</td></tr>")
        s.append("</table><div class='note'>Δ=<b>FAで補強し尽くした場合と比べた</b>スタメン合計pt/週の改善幅(ブレンド値)。"
                 "FAでタダで手に入る改善はトレードの価値に数えない(機会費用を反映)。双方ともトレード後にFA補強できる前提で評価。"
                 "◎は双方プラスで即打診可、○は相手が微マイナス(週2pt以内)なので売り文句付きで交渉。"
                 "Δ優勝%は上位案のみロスター入替後の再シミュレーションで算出。</div></div>")
    else:
        s.append("<div class='card'><div class='note'>現在の条件(FA補強後と比べて自分がプラス かつ 相手の損失が週2pt以内)を満たす案はありません。"
                 "= いま欲しいものはFAで拾う方が安い状態。戦力バランスが変わると候補が出てきます。</div></div>")

    s.append(f"<div class='sub' style='margin:20px 0'>アーカイブ: <a href='./dashboard/'>dashboard/</a>"
             f" / <a href='{esc(ctx['report_href'])}'>週次レポート</a></div>")
    s.append("</div>")
    if hover_js:
        s.append(hover_js)
    s.append("</body></html>")
    return "".join(s)
