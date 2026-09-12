"""Shared navigation and footer for the dependency-free dashboards."""


_STYLE = """<style>
body{min-height:100vh;display:flex;flex-direction:column}
body>main{width:100%;flex:1}
.site-header{width:100%;max-width:none;margin:0;padding:0;border-bottom:1px solid var(--line);background:#0b1020e6}
.site-bar{width:100%;max-width:1400px;margin:auto;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:24px}
.site-brand{display:inline-flex;align-items:center;gap:12px;color:var(--text);text-decoration:none;font-size:17px;font-weight:700;white-space:nowrap}
.site-mark{display:grid;place-items:center;width:38px;height:38px;border:1px solid #327568;border-radius:12px;background:linear-gradient(145deg,#214f4a,#152e32);color:var(--accent)}
.site-nav{display:flex;flex-wrap:wrap;gap:6px}
.site-nav a{padding:10px 13px;border:1px solid transparent;border-radius:8px;color:var(--muted);text-decoration:none;font-size:14px;font-weight:600}
.site-nav a:hover{color:var(--text);background:var(--panel)}
.site-nav a[aria-current]{color:var(--accent);background:#16332f;border-color:#327568}
.site-header a:focus-visible,.site-footer a:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.page-heading{width:100%}
.site-footer{margin-top:40px;border-top:1px solid var(--line);background:#0b102080}
.site-footer .site-bar{padding-top:28px;padding-bottom:28px}
.site-footer strong{display:block;margin-bottom:6px;font-size:14px;font-weight:600}
.site-footer p{margin:0;color:var(--muted);font-size:13px;line-height:1.6}
.site-footer nav{display:flex;gap:24px;font-size:13px}
.site-footer a{color:var(--muted);text-decoration:none}
.site-footer a:hover{color:var(--accent)}
@media(max-width:850px){.site-bar{align-items:flex-start;flex-direction:column;gap:18px}}
@media(max-width:650px){.site-bar{padding:16px}.site-nav{gap:4px}.site-nav a{padding:10px}.site-footer{margin-top:24px}}
</style>"""

_FOOTER = """<footer class="site-footer"><div class="site-bar">
<div><strong>IBKR Portfolio</strong><p>Your portfolio, with a clear view of every move.</p></div>
<nav aria-label="Footer"><a href="/ui">Home</a><a href="/docs">API docs</a></nav>
</div></footer>"""


def render_ui_page(html: str, active_page: str = "") -> str:
    """Add the common chrome while preserving each page's content and scripts."""

    links = "".join(
        f'<a href="{href}"' + (' aria-current="page"' if href == active_page else '')
        + f'>{label}</a>'
        for href, label in (
            ("/ui", "Home"),
            ("/ui/costs", "Costs"),
            ("/ui/transfers", "Transfers"),
            ("/ui/operations", "Operations"),
            ("/ui/ingestion-runs", "Ingestion runs"),
        )
    )
    header = """<header class="site-header"><div class="site-bar">
<a class="site-brand" href="/ui"><span class="site-mark" aria-hidden="true">
<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 17l6-6 4 3 6-8M15 6h5v5"/></svg>
</span>IBKR Portfolio</a><nav class="site-nav" aria-label="Main navigation">""" + links + "</nav></div></header>"
    return (
        html.replace("</head>", _STYLE + "</head>")
        .replace("<body>", "<body>" + header)
        .replace("</body>", _FOOTER + "</body>")
    )
