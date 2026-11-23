"""Self-contained HTML report you can open in any browser."""
from __future__ import annotations

import html
import time
from pathlib import Path

from .explain import explain
from .model import Finding, Severity
from .scanner import Inventory


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def plain(f: Finding) -> str:
    e = explain(f)
    parts = [f"<p><b>What's wrong:</b> {html.escape(e.wrong)}</p>"]
    if f.fixable and e.fix:
        parts.append(f"<p><b>What the fix does:</b> {html.escape(e.fix)}</p>")
    if e.safety:
        parts.append(f"<p><b>Is it safe?</b> {html.escape(e.safety)}</p>")
    opts = "".join(f"<li><b>{html.escape(n)}:</b> {html.escape(w)}</li>" for n, w in e.options)
    parts.append(f"<p><b>Your choices:</b></p><ul class=\"opts\">{opts}</ul>")
    return "".join(parts)


def render(inv: Inventory, findings: list[Finding]) -> str:
    counts = {s: sum(1 for f in findings if f.severity == s) for s in Severity}
    n_pkg, n_scr = len(inv.packages()), len(inv.scripts())
    rows = []
    for f in findings:
        files = "".join(f"<li>{html.escape(_rel(p, inv.sims_dir))}</li>" for p in f.files[:30])
        if len(f.files) > 30:
            files += f"<li>... and {len(f.files) - 30} more</li>"
        fix = ""
        if f.fixable:
            how = "included in <code>bettersims fix --apply</code>" if f.auto else \
                f"pick it with <code>bettersims fix --apply --only {f.id(inv.sims_dir)}</code>"
            fix = f'<p class="fix"><b>Fix:</b> {html.escape(f.fix_label)} <span class="how">({how})</span></p>'
        rows.append(
            f'<details class="f {f.severity.value}" {"open" if f.severity != Severity.INFO else ""}>'
            f'<summary><span class="tag">{f.severity.value}</span> {html.escape(f.title)}'
            f'<span class="id">{f.id(inv.sims_dir)}</span></summary>'
            f"{plain(f)}{fix}<p class=\"tech\">{html.escape(f.detail)}</p><ul>{files}</ul></details>"
        )
    body = "\n".join(rows) or '<p class="ok">No problems found. Your Mods folder looks healthy.</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BetterSims report</title>
<style>
:root{{--bg:#f7f7fb;--card:#fff;--fg:#1d1d27;--muted:#6b6b7b;--crit:#c62828;--warn:#b26a00;--info:#2e6fbd;--line:#e3e3ec}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15151c;--card:#1f1f29;--fg:#ececf3;--muted:#9a9aab;--line:#33333f;--crit:#ff6b6b;--warn:#ffb74d;--info:#64a8ff}}}}
body{{font:15px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px 16px}}
main{{max-width:960px;margin:auto}} h1{{margin:0 0 4px}} .muted{{color:var(--muted)}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}}
.stat b{{font-size:22px;display:block}}
.f{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--info);border-radius:8px;margin:10px 0;padding:10px 14px}}
.f.critical{{border-left-color:var(--crit)}} .f.warning{{border-left-color:var(--warn)}}
summary{{cursor:pointer;font-weight:600}} .tag{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:2px 6px;border-radius:4px;border:1px solid currentColor;margin-right:6px}}
.critical .tag{{color:var(--crit)}} .warning .tag{{color:var(--warn)}} .info .tag{{color:var(--info)}}
.id{{float:right;font:12px monospace;color:var(--muted)}} ul{{font:13px monospace;color:var(--muted);overflow-wrap:anywhere}}
.fix{{background:var(--bg);padding:6px 10px;border-radius:6px}} .how{{color:var(--muted);font-size:13px}} code{{font-size:13px}}
.ok{{font-size:18px}} .tech{{color:var(--muted);font-size:13px}} .opts{{font:inherit;color:inherit}}
</style></head><body><main>
<h1>BetterSims report</h1>
<p class="muted">{html.escape(str(inv.sims_dir))} &middot; {time.strftime("%Y-%m-%d %H:%M")}</p>
<div class="stats">
<div class="stat"><b>{n_pkg}</b>packages</div><div class="stat"><b>{n_scr}</b>script mods</div>
<div class="stat"><b style="color:var(--crit)">{counts[Severity.CRITICAL]}</b>critical</div>
<div class="stat"><b style="color:var(--warn)">{counts[Severity.WARNING]}</b>warnings</div>
<div class="stat"><b style="color:var(--info)">{counts[Severity.INFO]}</b>info</div></div>
<p class="muted">Fixes never delete anything. Files go to BetterSims/Quarantine and <code>bettersims undo</code> puts them back.</p>
{body}
</main></body></html>"""


def write(inv: Inventory, findings: list[Finding], path: Path) -> Path:
    path = Path(path)
    path.write_text(render(inv, findings), encoding="utf-8")
    return path
