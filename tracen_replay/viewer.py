"""Self-contained HTML viewer; reports work without a server or network."""

from html import escape
from .accounting_view import render_accounting


def timestamp(ms):
    minutes, remainder = divmod(ms, 60000)
    return f"{minutes:02}:{remainder / 1000:06.3f}"


def render(report):
    accounting = render_accounting(report)
    notice = ('<strong>Experimental stat tracking is enabled.</strong> Checkpoints use repeated OCR readings; explanations come from independently read outcome text. General screen recognition is not implemented.' if report.get('stat_tracking') else '<strong>Screen recognition is not enabled.</strong> Captured frames remain unclassified. Imported annotations are reference observations, not model predictions. Sampling can miss brief screens.')
    observations = []
    for item in report["observations"]:
        evidence = (f'<a href="{escape(item["evidence"], quote=True)}"><img loading="lazy" src="{escape(item["evidence"], quote=True)}" alt="Evidence at {timestamp(item["evidence_timestamp_ms"])}"></a>'
                    if item["evidence"] else '<p class="missing">No exact sample matches this annotation. Inspect the source before assigning its label to a nearby frame.</p>')
        observations.append(f'<article><h3>{timestamp(item["source_timestamp_ms"])} — {escape(item["title"])}</h3><p class="meta">Clip +{timestamp(item["clip_timestamp_ms"])} · {escape(item["screen_label"] or "Unknown")} · {escape(item["annotation_method"])}</p><p>{escape(item["note"])}</p>{evidence}</article>')
    frames = []
    for frame in report["frames"]:
        frames.append(f'<figure><a href="{frame["evidence"]}"><img loading="lazy" src="{frame["evidence"]}" alt="Unclassified frame at source {timestamp(frame["source_timestamp_ms"])}"></a><figcaption>Source {timestamp(frame["source_timestamp_ms"])} · Clip +{timestamp(frame["clip_timestamp_ms"])}</figcaption></figure>')
    source = report['source']
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tracen Replay — local evidence</title><style>
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:9px;border-bottom:1px solid #cad1c8}th:first-child,td:first-child{text-align:left}pre{white-space:pre-wrap}details{padding:8px 0}body{margin:0;background:#f5f6f2;color:#222a25;font:16px/1.6 system-ui,sans-serif}main{max-width:1120px;margin:0 auto;padding:32px 24px}h1{font-size:32px;line-height:1.2}h2{margin-top:40px}h3{font-size:20px}.meta,figcaption{color:#4b5d50;font-size:14px}a{color:#145b3c}img{display:block;width:100%;height:auto}article{border-top:1px solid #cad1c8;padding:16px 0;margin:24px 0}figure{margin:0}#frames{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}nav{display:flex;gap:24px;flex-wrap:wrap}.missing{border-left:3px solid #80571c;padding-left:12px}code{overflow-wrap:anywhere}@media(max-width:700px){#frames{grid-template-columns:1fr}main{padding:24px 16px}}
</style></head><body><main>'''+f'''<h1>Tracen Replay</h1><p>Local video evidence · {escape(source['name'])}</p><p class="meta">{source['width']} × {source['height']} · Source {timestamp(report['clip']['source_start_ms'])}–{timestamp(report['clip']['source_start_ms']+report['clip']['duration_ms'])} · {len(report['frames'])} samples</p><p>{notice}</p><nav><a href="#observations">Reference annotations ({len(observations)})</a><a href="#samples">All samples</a><a href="report.json">JSON report</a></nav>{accounting}<h2 id="observations">Reference annotations</h2>'''+(''.join(observations) or '<p>No annotations were supplied. All timestamped evidence is available below.</p>')+'<h2 id="samples">All samples · unclassified</h2><p>Click any image to inspect it at full resolution. Times refer to decoded video presentation timestamps.</p><div id="frames">'+''.join(frames)+'</div></main></body></html>'
