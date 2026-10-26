"""HTML dashboard generator — a self-contained performance page.

Writes docs/index.html (no CDN, no JS build — inline CSS + one inline SVG
equity curve) so GitHub Pages can serve it directly from the repo.
Sections: KPI cards, equity curve, open positions, closed trades,
backtest summary, and the latest trending board snapshot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .. import __version__
from ..config import Config
from ..logger import get_logger

log = get_logger("dashboard")

FA_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")


def _fa(value) -> str:
    return str(value).translate(FA_DIGITS)


def _fmt_price(v) -> str:
    if v is None:
        return "—"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def _svg_curve(curve: list[dict], width: int = 860, height: int = 220) -> str:
    """Minimal dependency-free equity curve as inline SVG."""
    if len(curve) < 2:
        return '<p class="muted">منحنی سرمایه پس از چند اجرا رسم می‌شود.</p>'
    eqs = [p["equity"] for p in curve]
    lo, hi = min(eqs), max(eqs)
    span = (hi - lo) or 1.0
    pad = 12
    step = (width - 2 * pad) / (len(eqs) - 1)
    pts = [
        (pad + i * step, height - pad - (eq - lo) / span * (height - 2 * pad))
        for i, eq in enumerate(eqs)
    ]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{height - pad} {poly} {pad + (len(eqs) - 1) * step:.1f},{height - pad}"
    color = "#22c55e" if eqs[-1] >= eqs[0] else "#ef4444"
    labels = ""
    first_t, last_t = curve[0].get("t", ""), curve[-1].get("t", "")
    labels += f'<text x="{pad}" y="{height - 1}" class="svglabel">{escape(first_t)}</text>'
    labels += f'<text x="{width - pad}" y="{height - 1}" class="svglabel" text-anchor="end">{escape(last_t)}</text>'
    return (
        f'<svg viewBox="0 0 {width} {height}" class="curve">'
        f'<polygon points="{area}" fill="{color}22" />'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linejoin="round" />'
        f'<text x="{pad}" y="16" class="svglabel">{_fa(round(eqs[-1], 2))}$</text>'
        f'{labels}</svg>'
    )


def _kpi(label: str, value: str, cls: str = "") -> str:
    return f'<div class="kpi {cls}"><div class="kpi-v">{value}</div><div class="kpi-l">{label}</div></div>'


def _trade_rows(trades: list[dict], max_rows: int = 20) -> str:
    if not trades:
        return '<tr><td colspan="8" class="muted">هنوز معامله‌ای ثبت نشده است.</td></tr>'
    rows = []
    for t in trades[:max_rows]:
        r = t.get("realized_r", 0)
        rcls = "pos" if r > 0 else ("neg" if r < 0 else "")
        badge = "🟢" if t["direction"] == "long" else "🔴"
        status = "باز" if t["status"] == "open" else {
            "tp1": "هدف ۱", "tp2": "هدف ۲", "tp3": "هدف ۳",
            "stop": "استاپ", "timeout": "انقضا",
        }.get(t.get("close_reason", ""), t.get("close_reason", ""))
        rows.append(
            f"<tr><td>{badge} <b>{escape(t['symbol'])}</b></td>"
            f"<td>{_fa(t.get('confidence') or 0)}٪</td>"
            f"<td>{_fa(_fmt_price(t.get('entry')))}</td>"
            f"<td>{_fa(_fmt_price(t.get('stop')))}</td>"
            f"<td class='{rcls}'>{_fa(round(r, 2))}R</td>"
            f"<td>{_fa(round(t.get('remaining_pct') or 0))}٪</td>"
            f"<td>{escape(status)}</td>"
            f"<td class='muted'>{escape((t.get('opened_at_utc') or '')[:16])}</td></tr>"
        )
    return "".join(rows)


def _backtest_section(root: Path) -> str:
    path = root / "data" / "backtest" / "summary.json"
    if not path.exists():
        return ""
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    rows = ""
    for p in summary.get("pairs", []):
        verdict_cls = "pos" if p.get("verdict") == "PASS" else "warn"
        rows += (
            f"<tr><td><b>{escape(p.get('pair', ''))}</b></td>"
            f"<td>{_fa(p.get('trades', 0))}</td>"
            f"<td>{_fa(p.get('win_rate') or 0)}٪</td>"
            f"<td class='{'pos' if (p.get('expectancy_r') or 0) > 0 else 'neg'}'>"
            f"{_fa(p.get('expectancy_r') or 0)}R</td>"
            f"<td>{_fa(p.get('profit_factor') or 0)}</td>"
            f"<td>{_fa(p.get('max_drawdown_r') or 0)}R</td>"
            f"<td class='{verdict_cls}'><b>{escape(p.get('verdict', ''))}</b></td></tr>"
        )
    if not rows:
        return ""
    return (
        "<h2>بک‌تست قوانین (دیتای واقعی بایننس)</h2>"
        "<table><thead><tr><th>جفت</th><th>تعداد</th><th>وین‌ریت</th>"
        "<th>امید ریاضی</th><th>PF</th><th>حداکثر افت</th><th>نتیجه</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _trending_section(root: Path) -> str:
    path = root / "data" / "trending_latest.json"
    if not path.exists():
        return ""
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    rows = ""
    for c in snap.get("coins", [])[:12]:
        rows += (
            f"<tr><td>{_fa(c.get('rank', ''))}</td><td><b>{escape(c.get('symbol', ''))}</b></td>"
            f"<td>{_fa(round(c.get('score', 0), 1))}</td>"
            f"<td class='{'pos' if (c.get('stats', {}).get('change_24h_pct') or 0) >= 0 else 'neg'}'>"
            f"{_fa(c.get('stats', {}).get('change_24h_pct') or 0)}٪</td></tr>"
        )
    ran_at = escape(snap.get("run_at_utc", ""))
    return (
        f"<h2>آخرین برد ترند <span class='muted small'>({ran_at} UTC)</span></h2>"
        "<table><thead><tr><th>#</th><th>نماد</th><th>امتیاز</th><th>۲۴ ساعت</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


CSS = """
:root{--bg:#0b1220;--card:#121a2b;--line:#1f2b40;--tx:#e5ecf8;--muted:#8aa0bf;
--green:#22c55e;--red:#ef4444;--gold:#f5c04e;--accent:#5b8cff}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--tx);font-family:Tahoma,Vazirmatn,sans-serif;
margin:0;padding:24px}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 10px;color:var(--gold)}
.muted{color:var(--muted)} .small{font-size:12px} .pos{color:var(--green)}
.neg{color:var(--red)} .warn{color:var(--gold)}
.badge{display:inline-block;background:var(--accent);color:#fff;border-radius:8px;
padding:2px 10px;font-size:12px;vertical-align:middle}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:10px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px;text-align:center}
.kpi-v{font-size:20px;font-weight:700}.kpi-l{font-size:12px;color:var(--muted);margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--muted);font-weight:400;text-align:right;padding:8px;border-bottom:1px solid var(--line)}
td{padding:8px;border-bottom:1px solid var(--line)}
svg.curve{width:100%;height:auto}
.svglabel{fill:var(--muted);font-size:11px}
footer{margin-top:28px;color:var(--muted);font-size:12px;text-align:center}
a{color:var(--accent);text-decoration:none}
"""


def generate_dashboard(cfg: Config, root: Path) -> Path:
    """Build docs/index.html from journal + backtest + trending data."""
    from ..paper.journal import PaperJournal  # local import: no cycle

    journal = PaperJournal(cfg, root)
    s = journal.stats()
    open_trades = journal.open_trades()
    closed = [t for t in journal.data["trades"] if t["status"] == "closed"]
    closed_sorted = sorted(closed, key=lambda t: t.get("closed_at_utc") or "", reverse=True)

    wr = f"{_fa(s['win_rate'])}٪" if s["win_rate"] is not None else "—"
    pf = _fa(s["profit_factor"]) if s["profit_factor"] is not None else "—"
    ret = s["return_pct"]
    ret_cls = "pos" if ret >= 0 else "neg"

    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KhosroAiTrader — داشبورد عملکرد</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>🔥 KhosroAiTrader <span class="badge">v{__version__}</span></h1>
<p class="muted">داشبورد عملکرد کاغذی (Paper Trading) — اجرای خودکار روی GitHub Actions ·
<a href="https://github.com/">ریپو</a></p>

<div class="cards">
{_kpi("سرمایه کاغذی", _fa(s['equity']) + "$")}
{_kpi("بازدهی کل", _fa(ret) + "٪", ret_cls)}
{_kpi("وین‌ریت", wr)}
{_kpi("فاکتور سود", pf)}
{_kpi("مجموع R", _fa(s['total_r']) + "R", "pos" if s["total_r"] >= 0 else "neg")}
{_kpi("معاملات باز", _fa(s['open_trades']))}
{_kpi("بسته‌شده", _fa(s['closed_trades']))}
{_kpi("حداکثر افت", _fa(s['max_drawdown_pct'] or 0) + "٪", "warn")}
</div>

<h2>منحنی سرمایه</h2>
<div class="card">{_svg_curve(journal.data.get("equity_curve", []))}</div>

<h2>معاملات باز ({_fa(len(open_trades))})</h2>
<div class="card"><table>
<thead><tr><th>نماد</th><th>اطمینان</th><th>ورود</th><th>استاپ</th>
<th>R تحقق‌یافته</th><th>باقی‌مانده</th><th>وضعیت</th><th>زمان</th></tr></thead>
<tbody>{_trade_rows(sorted(open_trades, key=lambda t: t.get("opened_at_utc", ""), reverse=True))}</tbody>
</table></div>

<h2>معاملات بسته‌شده (آخرین {_fa(min(20, len(closed_sorted)))})</h2>
<div class="card"><table>
<thead><tr><th>نماد</th><th>اطمینان</th><th>ورود</th><th>استاپ</th>
<th>R</th><th>باقی‌مانده</th><th>خروج</th><th>زمان</th></tr></thead>
<tbody>{_trade_rows(closed_sorted)}</tbody>
</table></div>

{_backtest_section(root)}
{_trending_section(root)}

<footer>ساخته‌شده در {escape(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))} UTC ·
KhosroAiTrader v{__version__} · داده‌ها کاغذی هستند و توصیه سرمایه‌گذاری نیستند</footer>
</div></body></html>"""

    out = (root / cfg.dashboard.output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    log.info("dashboard written: %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out
