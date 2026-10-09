"""Render concise evidence-led comparison reports as standalone HTML."""

import base64
from datetime import date
from html import escape
from pathlib import Path

from jinja2 import Environment, BaseLoader, select_autoescape
from markdown_it import MarkdownIt

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{{ title }}</title><style>
:root{color-scheme:light;--ink:#14233b;--muted:#63738b;--line:#e2e8f0;--soft:#f5f8fc;--blue:#3858df;--green:#087f66;--amber:#ad5b08}
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{width:min(1120px,calc(100% - 36px));margin:34px auto 56px}.hero{position:relative;overflow:hidden;padding:34px 38px;border-radius:24px;color:#fff;background:linear-gradient(122deg,#172d58,#314fbd 62%,#5d77e8);box-shadow:0 20px 44px #233e751c}
.hero:after{content:"";position:absolute;width:330px;height:330px;right:-90px;top:-200px;border:1px solid #ffffff33;border-radius:50%;box-shadow:0 0 0 35px #ffffff0b,0 0 0 75px #ffffff08}
.eyebrow{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#b9c8ff}.hero h1{font-size:clamp(28px,4vw,42px);line-height:1.12;letter-spacing:-.035em;margin:12px 0 9px;max-width:800px}.hero p{margin:0;color:#d9e2ff}.hero-meta{display:flex;gap:10px;align-items:center;margin-top:22px;font-size:12px;color:#e0e7ff}.hero-meta span{border:1px solid #ffffff38;background:#ffffff13;padding:5px 10px;border-radius:99px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}.stat,.panel{background:white;border:1px solid var(--line);border-radius:17px;box-shadow:0 5px 18px #1d35500a}.stat{padding:17px 19px}.stat b{display:block;font-size:24px;letter-spacing:-.04em}.stat span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-weight:750}
.panel{padding:23px 25px;margin-top:15px}.panel h2{margin:0 0 15px;font-size:18px;letter-spacing:-.02em}.panel-intro{margin:-7px 0 17px;color:var(--muted);font-size:13px}.matrix{overflow:auto;border:1px solid var(--line);border-radius:12px}table{border-collapse:collapse;width:100%;min-width:670px}th,td{padding:12px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#718096;background:#f6f8fc}td{font-size:13px}tr:last-child td{border-bottom:0}.dimension{font-weight:750;white-space:nowrap}.value{min-width:210px}.verdict{display:inline-block;padding:3px 8px;border-radius:99px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;background:#edf2f8;color:#516176}.verdict-a,.verdict-b{background:#e7f7f1;color:#087f66}.verdict-tie{background:#eef2ff;color:#4257bc}.verdict-unknown,.verdict-not_comparable{background:#fff4df;color:#996008}.evidence-links{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}.evidence-links a,.source-chip{font-size:10px;color:#5369c7;text-decoration:none;border:1px solid #dce3f5;border-radius:99px;padding:2px 7px}
.benchmarks{display:grid;gap:13px}.benchmark{display:grid;grid-template-columns:180px 1fr;gap:12px;align-items:center}.benchmark-name{font-size:12px;font-weight:700}.bar-stack{display:grid;gap:5px}.bar-row{display:grid;grid-template-columns:112px 1fr 34px;gap:8px;align-items:center;font-size:10px;color:var(--muted)}.bar{height:7px;border-radius:8px;background:#edf1f7;overflow:hidden}.bar i{display:block;height:100%;border-radius:inherit;background:var(--blue)}.bar-row.b .bar i{background:#14a27d}.score{font-variant-numeric:tabular-nums;text-align:right}
.screenshots{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:13px}.shot{margin:0;overflow:hidden;border:1px solid var(--line);border-radius:13px;background:#f8fafd}.shot img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;object-position:top}.shot figcaption{padding:11px 13px;font-size:12px;color:#34445c}.shot figcaption b{display:block;color:var(--ink);margin-bottom:2px}
.sources{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:9px}.source{padding:13px;border:1px solid var(--line);border-radius:12px;background:#fbfcfe}.source a{color:#3154c9;font-size:12px;font-weight:750;overflow-wrap:anywhere}.source p{font-size:12px;color:#526278;margin:7px 0 0}.source small{color:#8a97aa}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}.metric-card{border:1px solid var(--line);border-radius:13px;padding:16px;background:#fbfcfe}.metric-card h3{margin:0 0 10px;font-size:15px}.metric-card h4{margin:14px 0 5px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#60708a}.metric-pills{display:flex;gap:7px;flex-wrap:wrap}.metric-pill{padding:7px 10px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:12px;color:#526278}.metric-pill b{color:var(--ink)}.metric-card ul{margin:6px 0;padding-left:18px;color:#526278;font-size:12px}.metric-card p{font-size:12px;color:var(--muted);margin:6px 0}.metric-card a{font-size:12px;color:#3154c9;font-weight:700}
details{margin-top:15px}summary{cursor:pointer;color:#4358b5;font-weight:750;font-size:13px}.narrative{padding-top:14px;color:#34445c}.narrative h1{font-size:24px}.narrative h2{font-size:18px;margin:27px 0 9px}.narrative h3{font-size:15px}.narrative p,.narrative li{font-size:13px}.narrative table{min-width:0}.narrative blockquote{margin:14px 0;padding:9px 15px;border-left:3px solid #aebcf6;background:#f7f8ff;color:#526278}.narrative a{color:#3456c8}.narrative code{background:#f1f4f8;padding:2px 5px;border-radius:4px}
.foot{padding:20px 5px;text-align:center;color:#8793a7;font-size:11px}.empty{color:var(--muted);font-size:13px;padding:12px 0}
@media(max-width:700px){.wrap{width:min(100% - 20px,1120px);margin:10px auto 30px}.hero{padding:25px 22px;border-radius:18px}.panel{padding:17px 15px}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.stat{padding:13px}.stat b{font-size:21px}.benchmark{grid-template-columns:1fr;gap:5px}.bar-row{grid-template-columns:88px 1fr 30px}}
</style></head><body><main class="wrap">
<header class="hero"><div class="eyebrow">Public evidence · product comparison</div><h1>{{ title }}</h1>
<p>{{ scope_note or "Side-by-side product research with source-linked findings and directly comparable benchmarks." }}</p>
<div class="hero-meta"><span>{{ scope_label }}</span><span>{{ report_date }}</span><span>{{ screenshot_count }} reviewed screenshots</span></div></header>
<section class="stats"><article class="stat"><span>Products</span><b>02</b></article><article class="stat"><span>Verified facts</span><b>{{ fact_count }}</b></article><article class="stat"><span>Source pages</span><b>{{ source_count }}</b></article><article class="stat"><span>Reviewed screenshots</span><b>{{ screenshot_count }}</b></article></section>
{% if matrix %}<section class="panel"><h2>Comparison at a glance</h2><p class="panel-intro">Values come from verified page evidence. “Unknown” means the public sources did not establish a fair comparison.</p><div class="matrix"><table><thead><tr><th>Dimension</th><th>{{ product_names[0] }}</th><th>{{ product_names[1] }}</th><th>Evidence read</th></tr></thead><tbody>{{ matrix|safe }}</tbody></table></div></section>{% endif %}
{% if benchmark_rows %}<section class="panel"><h2>Evidence-backed benchmarks</h2><p class="panel-intro">Scores appear only where both products have a directly comparable, sourced basis.</p><div class="benchmarks">{% for row in benchmark_rows %}<div class="benchmark"><div class="benchmark-name">{{ row.dimension }}</div><div class="bar-stack"><div class="bar-row"><span>{{ product_names[0] }}</span><div class="bar"><i style="width:{{ row.score_a }}%"></i></div><span class="score">{{ row.score_a }}</span></div><div class="bar-row b"><span>{{ product_names[1] }}</span><div class="bar"><i style="width:{{ row.score_b }}%"></i></div><span class="score">{{ row.score_b }}</span></div></div></div>{% endfor %}</div></section>{% endif %}
{% if not benchmark_rows and matrix %}<section class="panel"><h2>Evidence-backed benchmarks</h2><p class="panel-intro">No numeric scores were assigned because the available facts do not provide a fair, directly comparable measurement. Product values and evidence links are shown in the comparison matrix.</p></section>{% endif %}
{% if site_metrics %}<section class="panel"><h2>Technical SEO and GitHub signals</h2><p class="panel-intro">PageSpeed scores are mobile Lighthouse lab audits, not keyword rankings or traffic estimates. GitHub metrics describe a matched public repository, not overall product quality.</p><div class="metric-grid">{% for item in site_metrics %}<article class="metric-card"><h3>{{ item.product }}</h3><h4>Technical SEO · PageSpeed Insights</h4>{% if item.pagespeed %}<div class="metric-pills">{% if item.pagespeed.seo_score is not none %}<span class="metric-pill">SEO audit <b>{{ item.pagespeed.seo_score }}/100</b></span>{% endif %}{% if item.pagespeed.performance_score is not none %}<span class="metric-pill">Mobile performance <b>{{ item.pagespeed.performance_score }}/100</b></span>{% endif %}</div>{% if item.pagespeed.failed_seo_audits %}<ul>{% for audit in item.pagespeed.failed_seo_audits[:5] %}<li>{{ audit.title }}{% if audit.display_value %}: {{ audit.display_value }}{% endif %}</li>{% endfor %}</ul>{% else %}<p>No failed SEO audits were returned.</p>{% endif %}<a href="{{ item.pagespeed.report_url }}" target="_blank" rel="noopener noreferrer">Open PageSpeed report</a>{% else %}<p>{% if item.pagespeed_error %}Technical SEO audit unavailable: {{ item.pagespeed_error }}{% else %}No PageSpeed data returned{% endif %}.</p>{% endif %}<h4>Public GitHub repository</h4>{% if item.github %}<div class="metric-pills"><span class="metric-pill">Stars <b>{{ item.github.stars }}</b></span><span class="metric-pill">Forks <b>{{ item.github.forks }}</b></span>{% if item.github.language %}<span class="metric-pill">Language <b>{{ item.github.language }}</b></span>{% endif %}{% if item.github.license %}<span class="metric-pill">License <b>{{ item.github.license }}</b></span>{% endif %}</div><p>{{ item.github.repository }} · updated {{ item.github.updated_at or 'date unavailable' }} · repository match {{ (item.github.match_confidence * 100)|round|int }}%</p><a href="{{ item.github.url }}" target="_blank" rel="noopener noreferrer">Open GitHub repository</a>{% else %}<p>{% if item.github_error %}Repository lookup unavailable: {{ item.github_error }}{% else %}No confidently matched public repository found{% endif %}.</p>{% endif %}</article>{% endfor %}</div></section>{% endif %}
{% if screenshots %}<section class="panel"><h2>Product screenshots</h2><p class="panel-intro">Captured from the public product pages with headless Chromium and checked for usability.</p><div class="screenshots">{% for shot in screenshots %}<figure class="shot"><img loading="lazy" src="{{ shot.data_url }}" alt="{{ shot.caption }}"><figcaption><b>{{ shot.product }}</b>{{ shot.caption }}</figcaption></figure>{% endfor %}</div></section>{% endif %}
{% if sources %}<section class="panel"><h2>Sources and evidence</h2><p class="panel-intro">{{ source_count }} public pages contributed {{ fact_count }} quote-verified observations.</p><div class="sources">{% for source in sources %}<article class="source"><a href="{{ source.url }}" target="_blank" rel="noopener noreferrer">{{ source.label }}</a><small> · {{ source.product }} · {{ source.count }} verified item(s)</small>{% if source.quotes %}<p>“{{ source.quotes[0] }}”</p>{% endif %}</article>{% endfor %}</div></section>{% endif %}
<details class="panel"><summary>Read full research notes and methodology</summary><div class="narrative">{{ body|safe }}</div></details>
<footer class="foot">Generated from public information on {{ report_date }}. Vendor statements are labeled as claims; missing evidence is shown as unknown.</footer>
</main></body></html>"""


def _comparison_matrix(rows: list[dict], facts: list[dict]) -> str:
    facts_by_id = {int(item["id"]): item for item in facts if item.get("id") is not None}
    output = []
    for item in rows:
        verdict = str(item.get("verdict", "unknown"))
        links = []
        for fact_id in item.get("evidence_fact_ids", []):
            fact = facts_by_id.get(int(fact_id))
            if fact and fact.get("source_url"):
                url = escape(str(fact["source_url"]), quote=True)
                links.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">F{int(fact_id)}</a>')
        evidence = f'<div class="evidence-links">{"".join(links)}</div>' if links else ""
        output.append(
            "<tr>"
            f'<td class="dimension">{escape(str(item.get("dimension", "")))}</td>'
            f'<td class="value">{escape(str(item.get("a_value", "Unknown")))}</td>'
            f'<td class="value">{escape(str(item.get("b_value", "Unknown")))}</td>'
            f'<td><span class="verdict verdict-{escape(verdict, quote=True)}">{escape(verdict.replace("_", " "))}</span>{evidence}</td>'
            "</tr>"
        )
    return "".join(output)


def render_html(markdown: str, title: str, report_date: date | None = None,
                screenshots: list[dict[str, str]] | None = None,
                benchmarks: list[dict] | None = None,
                product_names: tuple[str, str] | None = None,
                facts: list[dict] | None = None,
                site_metrics: list[dict] | None = None,
                scope_label: str = "Verified recent launches",
                scope_note: str = "") -> str:
    """Render the visual evidence summary and expandable full Markdown report."""
    renderer = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    body_html = renderer.render(markdown)
    env = Environment(loader=BaseLoader(), autoescape=select_autoescape(default=True))
    template = env.from_string(TEMPLATE)
    embedded = []
    for shot in screenshots or []:
        path = Path(shot["path"])
        if path.is_file():
            embedded.append({"data_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
                             "caption": shot.get("caption", ""), "product": shot.get("product", "")})
    names = product_names or (title.split(" vs ", 1)[0], title.split(" vs ", 1)[-1])
    fact_rows = facts or []
    source_groups: dict[str, dict] = {}
    for fact in fact_rows:
        url = str(fact.get("source_url", ""))
        if not url:
            continue
        group = source_groups.setdefault(url, {"url": url, "label": url, "product": fact.get("product", "Product"),
                                                "count": 0, "quotes": []})
        group["count"] += 1
        quote = str(fact.get("evidence_quote", "")).strip()
        if quote and len(group["quotes"]) < 1:
            group["quotes"].append(quote[:260])
    benchmark_rows = [{"dimension": str(item.get("dimension", "Benchmark")),
                       "score_a": max(0, min(100, float(item["score_a"]))),
                       "score_b": max(0, min(100, float(item["score_b"]))) }
                      for item in (benchmarks or [])
                      if item.get("score_a") is not None and item.get("score_b") is not None]
    return template.render(title=title, body=body_html,
                           report_date=(report_date or date.today()).isoformat(),
                           screenshots=embedded, screenshot_count=len(embedded),
                           product_names=names, fact_count=len(fact_rows), source_count=len(source_groups),
                           sources=list(source_groups.values()), matrix=_comparison_matrix(benchmarks or [], fact_rows),
                           benchmark_rows=benchmark_rows, site_metrics=site_metrics or [],
                           scope_label=scope_label, scope_note=scope_note)
