"""Self-contained HTML report: every step with its screenshot, plus the run video."""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from .models import RunSummary, StepStatus, TestCase

MAX_EMBED_VIDEO = 25 * 1024 * 1024


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def render(summary: RunSummary, test: TestCase, run_dir: Path) -> str:
    e = html.escape
    passed = sum(1 for r in summary.results if r.status == StepStatus.PASSED)
    failed = sum(1 for r in summary.results if r.status == StepStatus.FAILED)
    skipped = sum(1 for r in summary.results if r.status == StepStatus.SKIPPED)

    video_html = ""
    if summary.video and (run_dir / summary.video).exists():
        vp = run_dir / summary.video
        mime = "video/webm" if vp.suffix == ".webm" else "video/mp4"
        src = f"data:{mime};base64,{_b64(vp)}" if vp.stat().st_size <= MAX_EMBED_VIDEO else e(summary.video)
        video_html = f'<section class="card"><h2>Recording</h2><video controls preload="metadata" src="{src}"></video></section>'

    rows = []
    for r in summary.results:
        st = r.step
        img = ""
        if r.screenshot and (run_dir / r.screenshot).exists():
            img = (f'<a class="shot" href="#" onclick="zoom(this);return false"><img loading="lazy" alt="Step {r.index + 1}" '
                   f'src="data:image/png;base64,{_b64(run_dir / r.screenshot)}"></a>')
        loc = ""
        if st.locator:
            loc = (f'<div class="kv"><span>locator</span><code>{e(st.locator.strategy)}={e(st.locator.value)}</code></div>'
                   f'<div class="kv"><span>element</span>{e(st.locator.description)}</div>')
        err = f'<pre class="err">{e(r.error)}</pre>' if r.error else ""
        healed = '<span class="tag heal">self-healed</span>' if r.healed else ""
        value = st.value
        if value and any(w in f"{st.target} {st.raw}".lower() for w in ("password", "secret", "token")):
            value = "••••••"
        rows.append(f"""
<article class="step {r.status.value}">
  <header><span class="num">{r.index + 1}</span><span class="status">{r.status.value}</span>
    <span class="raw">{e(st.raw or st.action.value)}</span>{healed}<span class="dur">{r.duration_ms} ms</span></header>
  <div class="body">
    <div class="meta">
      <div class="kv"><span>action</span><b>{e(st.action.value)}</b></div>
      {f'<div class="kv"><span>target</span>{e(st.target)}</div>' if st.target else ''}
      {f'<div class="kv"><span>value</span><code>{e(value)}</code></div>' if value else ''}
      {loc}
      {f'<div class="kv"><span>url</span><code>{e(r.url)}</code></div>' if r.url else ''}
      {err}
    </div>
    {img}
  </div>
</article>""")

    status_cls = summary.status.value
    data_json = e(json.dumps(test.data, indent=2)) if test.data else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(summary.test_name)} – {status_cls}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1c2230;--mut:#677085;--line:#e3e6ec;--ok:#1a7f4b;--bad:#c62f3c;--skip:#8a8f9c;--acc:#3a5bd9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11141a;--card:#1a1e27;--fg:#e6e9ef;--mut:#9aa3b5;--line:#2b303c;--ok:#3ccf87;--bad:#ff6b78;--acc:#7b96ff}}}}
*{{box-sizing:border-box}}body{{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:1150px;margin:0 auto;padding:24px 16px 64px}}
h1{{margin:0 0 4px;font-size:22px}}h2{{font-size:15px;margin:0 0 12px}}
.sub{{color:var(--mut)}}.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}}
.stats{{display:flex;gap:12px;flex-wrap:wrap}}.stat{{flex:1;min-width:120px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.stat b{{display:block;font-size:22px}}.badge{{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:600;color:#fff}}
.badge.passed{{background:var(--ok)}}.badge.failed{{background:var(--bad)}}
video{{width:100%;border-radius:8px;background:#000}}
.step{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--skip);border-radius:10px;margin:10px 0;overflow:hidden}}
.step.passed{{border-left-color:var(--ok)}}.step.failed{{border-left-color:var(--bad)}}
.step header{{display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}}
.num{{font-weight:700;color:var(--mut);min-width:22px}}.status{{text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.04em}}
.passed .status{{color:var(--ok)}}.failed .status{{color:var(--bad)}}.skipped .status{{color:var(--skip)}}
.raw{{flex:1;min-width:200px}}.dur{{color:var(--mut);font-size:12px}}
.tag{{font-size:11px;padding:1px 8px;border-radius:99px}}.heal{{background:#fff3cd;color:#7a5b00}}
.body{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:16px;padding:14px}}
@media (max-width:760px){{.body{{grid-template-columns:1fr}}}}
.kv{{display:flex;gap:8px;margin:3px 0;word-break:break-all}}.kv span{{color:var(--mut);min-width:64px}}
code{{font:12px ui-monospace,Menlo,Consolas,monospace;background:var(--bg);padding:1px 5px;border-radius:4px}}
.err{{white-space:pre-wrap;background:color-mix(in srgb,var(--bad) 12%,transparent);color:var(--bad);padding:8px;border-radius:6px;font-size:12px}}
.shot img{{width:100%;border:1px solid var(--line);border-radius:6px;cursor:zoom-in}}
#lb{{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;padding:16px;cursor:zoom-out}}
#lb img{{max-width:100%;max-height:100%}}pre.src{{white-space:pre-wrap;font-size:12px}}
</style></head><body><main>
<h1>{e(summary.test_name)} <span class="badge {status_cls}">{status_cls.upper()}</span></h1>
<div class="sub">Run <code>{e(summary.run_id)}</code> · engine <b>{e(summary.engine)}</b> · mode <b>{e(summary.mode)}</b> ·
agent <b>{e(summary.agent)}</b> · {e(summary.started_at)}</div>
<div class="stats" style="margin-top:16px">
<div class="stat"><span class="sub">Steps</span><b>{len(summary.results)}</b></div>
<div class="stat"><span class="sub">Passed</span><b style="color:var(--ok)">{passed}</b></div>
<div class="stat"><span class="sub">Failed</span><b style="color:var(--bad)">{failed}</b></div>
<div class="stat"><span class="sub">Skipped</span><b>{skipped}</b></div>
<div class="stat"><span class="sub">Duration</span><b>{summary.duration_ms / 1000:.1f}s</b></div>
</div>
{video_html}
<section><h2 style="margin-top:24px">Steps</h2>{''.join(rows)}</section>
<section class="card"><h2>Instructions</h2><pre class="src">{e(test.source_text or '')}</pre>
{f'<h2>Data</h2><pre class="src">{data_json}</pre>' if data_json else ''}</section>
</main><div id="lb" onclick="this.style.display='none'"><img alt=""></div>
<script>function zoom(a){{const lb=document.getElementById('lb');lb.querySelector('img').src=a.querySelector('img').src;lb.style.display='flex'}}</script>
</body></html>"""
