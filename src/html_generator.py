import html
import re
from datetime import datetime
from pathlib import Path


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _format_timestamp(start: float | None, end: float | None) -> str:
    if start is None:
        return ""
    sm, ss = divmod(int(start), 60)
    sh, sm = divmod(sm, 60)
    if end is not None:
        em, es = divmod(int(end), 60)
        eh, em = divmod(em, 60)
        if sh:
            return f"[{sh}:{sm:02d}:{ss:02d} - {eh}:{em:02d}:{es:02d}]"
        return f"[{sm:02d}:{ss:02d} - {em:02d}:{es:02d}]"
    if sh:
        return f"[{sh}:{sm:02d}:{ss:02d}]"
    return f"[{sm:02d}:{ss:02d}]"


def _merge_annotations(
    word_annotations: list[dict],
    phrase_annotations: list[dict],
) -> list[dict]:
    merged: list[dict] = []
    for a in word_annotations:
        merged.append({**a, "_sort": a.get("start_pos", 0)})
    for p in phrase_annotations:
        merged.append({**p, "_sort": p.get("start_pos", 0)})
    merged.sort(key=lambda x: x.get("_sort", 0))
    return merged


def _render_annotated_text(text: str, annotations: list[dict], phrase_annotations: list[dict] | None = None) -> str:
    if phrase_annotations is None:
        phrase_annotations = []

    all_items = _merge_annotations(annotations, phrase_annotations)
    if not all_items:
        return html.escape(text)

    parts: list[str] = []
    last_end = 0

    for item in all_items:
        start = item.get("start_pos", 0)
        end = item.get("end_pos", start)
        if start < last_end:
            continue

        parts.append(html.escape(text[last_end:start]))

        item_type = item.get("type", "word")

        if item_type == "phrase":
            phrase_text = item.get("phrase", text[start:end])
            chinese = item.get("chinese_definition", "")
            if chinese:
                parts.append(
                    f'<span class="learn-phrase" '
                    f'data-meaning="{html.escape(chinese)}">'
                    f'{html.escape(phrase_text)}'
                    f'<span class="phrase-annotation">[{html.escape(chinese)}]</span>'
                    f'</span>'
                )
            else:
                parts.append(
                    f'<span class="learn-phrase">'
                    f'{html.escape(phrase_text)}</span>'
                )
        else:
            word = item.get("word", text[start:end])
            ipa = item.get("ipa", "")
            cefr = item.get("cefr_level", "")
            chinese = item.get("chinese_definition", "")

            annotation_parts = []
            if ipa:
                annotation_parts.append(ipa)
            if chinese:
                annotation_parts.append(chinese)
            annotation_str = " ".join(annotation_parts)

            cefr_class = f"cefr-{cefr.lower()}" if cefr else "cefr-unknown"

            if annotation_str:
                parts.append(
                    f'<span class="difficult-word {cefr_class}" '
                    f'data-cefr="{html.escape(cefr)}">'
                    f'{html.escape(word)}'
                    f'<span class="annotation">[{html.escape(annotation_str)}]</span>'
                    f'</span>'
                )
            else:
                parts.append(
                    f'<span class="difficult-word {cefr_class}" '
                    f'data-cefr="{html.escape(cefr)}">'
                    f'{html.escape(word)}</span>'
                )
        last_end = end

    parts.append(html.escape(text[last_end:]))
    return "".join(parts)


_CSS = """
@page {
    size: A4;
    margin: 1.2cm 1cm;
}

* { box-sizing: border-box; }

body {
    font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.5;
    color: #1a1a1a;
    max-width: 210mm;
    margin: 0 auto;
    padding: 0.4cm;
    background: #fff;
}

.document-header {
    border-bottom: 2px solid #2c3e50;
    padding-bottom: 8px;
    margin-bottom: 14px;
}

.video-title {
    font-size: 15pt;
    font-weight: 700;
    color: #2c3e50;
    margin: 0 0 6px 0;
    line-height: 1.3;
}

.video-meta {
    font-size: 8pt;
    color: #666;
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 2px;
}

.video-meta .meta-item {
    display: inline-flex;
    align-items: center;
    gap: 3px;
}

.video-url {
    font-size: 7pt;
    color: #999;
    word-break: break-all;
    margin-top: 2px;
}

.content-grid {
    display: grid;
    grid-template-columns: 1fr 26%;
    gap: 0 10px;
}

.subtitle-column {
    min-width: 0;
}

.segment {
    margin-bottom: 5px;
    padding: 2px 0;
    border-bottom: 1px dotted #e8e8e8;
    page-break-inside: avoid;
}

.segment:last-child {
    border-bottom: none;
}

.timestamp {
    font-size: 7pt;
    color: #aaa;
    font-family: "Consolas", "Courier New", monospace;
}

.english-line {
    font-size: 10pt;
    line-height: 1.45;
    margin: 1px 0 1px 0;
    color: #1a1a1a;
}

.chinese-line {
    font-size: 8.5pt;
    line-height: 1.4;
    margin: 0 0 0 8px;
    color: #666;
}

.notes-column {
    border-left: 1px dotted #ccc;
    padding-left: 8px;
}

.notes-header {
    font-size: 7pt;
    color: #bbb;
    text-align: center;
    margin-bottom: 4px;
    font-style: italic;
}

.notes-line {
    border-bottom: 1px dotted #ddd;
    height: 20px;
}

.notes-divider {
    border-bottom: 1px solid #ccc;
    height: 20px;
    margin-bottom: 2px;
}

.difficult-word {
    color: #c0392b;
    font-weight: 600;
    text-decoration: underline;
    text-decoration-color: #e74c3c;
    text-underline-offset: 2px;
    text-decoration-thickness: 1px;
    cursor: default;
    position: relative;
}

.difficult-word.cefr-b1 { color: #e67e22; text-decoration-color: #f39c12; }
.difficult-word.cefr-b2 { color: #c0392b; text-decoration-color: #e74c3c; }
.difficult-word.cefr-c1 { color: #8e44ad; text-decoration-color: #9b59b6; }
.difficult-word.cefr-c2 { color: #8e44ad; text-decoration-color: #9b59b6; font-weight: 700; }

.annotation {
    font-size: 7pt;
    color: #8e44ad;
    font-weight: 400;
    vertical-align: super;
    margin-left: 1px;
    font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
}

.learn-phrase {
    background: linear-gradient(to bottom, transparent 60%, #d4edda 60%);
    color: #155724;
    font-weight: 500;
    cursor: default;
    position: relative;
    border-radius: 2px;
    padding: 0 2px;
}

.phrase-annotation {
    font-size: 7pt;
    color: #0c6e3a;
    font-weight: 400;
    vertical-align: super;
    margin-left: 2px;
    font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
}

.word-list {
    page-break-before: always;
    grid-column: 1 / -1;
    margin-top: 20px;
}

.word-list-title {
    font-size: 12pt;
    color: #2c3e50;
    border-bottom: 2px solid #2c3e50;
    padding-bottom: 4px;
    margin-bottom: 8px;
}

.word-list table {
    width: 100%;
    border-collapse: collapse;
    font-size: 9pt;
}

.word-list th {
    background: #2c3e50;
    color: white;
    padding: 4px 8px;
    text-align: left;
    font-weight: 600;
}

.word-list td {
    border: 1px solid #e0e0e0;
    padding: 3px 8px;
}

.word-list tr:nth-child(even) {
    background: #f8f9fa;
}

.word-list .word-cell {
    color: #c0392b;
    font-weight: 600;
}

.word-list .ipa-cell {
    font-family: "Consolas", "Courier New", monospace;
    font-size: 8.5pt;
    color: #555;
}

.word-list .cefr-cell {
    text-align: center;
    font-weight: 600;
    font-size: 8pt;
}

.cefr-badge {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    color: white;
    font-size: 7pt;
}

.cefr-badge.a1 { background: #27ae60; }
.cefr-badge.a2 { background: #2ecc71; }
.cefr-badge.b1 { background: #f39c12; }
.cefr-badge.b2 { background: #e67e22; }
.cefr-badge.c1 { background: #9b59b6; }
.cefr-badge.c2 { background: #8e44ad; }
.cefr-badge.unknown { background: #95a5a6; }

.phrase-list {
    page-break-before: always;
    grid-column: 1 / -1;
    margin-top: 16px;
}

.phrase-list-title {
    font-size: 12pt;
    color: #155724;
    border-bottom: 2px solid #28a745;
    padding-bottom: 4px;
    margin-bottom: 8px;
}

.phrase-list table {
    width: 100%;
    border-collapse: collapse;
    font-size: 9pt;
}

.phrase-list th {
    background: #28a745;
    color: white;
    padding: 4px 8px;
    text-align: left;
    font-weight: 600;
}

.phrase-list td {
    border: 1px solid #e0e0e0;
    padding: 3px 8px;
}

.phrase-list tr:nth-child(even) {
    background: #f0fff4;
}

.phrase-list .phrase-cell {
    color: #155724;
    font-weight: 600;
}

.legend {
    margin-top: 10px;
    padding: 8px 10px;
    background: #f8f9fa;
    border-radius: 4px;
    font-size: 8pt;
    color: #555;
}

.legend-title {
    font-weight: 600;
    margin-bottom: 3px;
    color: #333;
}

.legend-item {
    display: inline-block;
    margin-right: 10px;
}

.legend-phrase-item {
    display: inline-block;
    margin-right: 10px;
}

.legend-phrase-sample {
    background: linear-gradient(to bottom, transparent 60%, #d4edda 60%);
    color: #155724;
    font-weight: 500;
    padding: 0 2px;
    border-radius: 2px;
}

@media print {
    body { max-width: none; padding: 0; margin: 0; font-size: 9.5pt; }
    .document-header { border-bottom: 2px solid #2c3e50; padding-bottom: 6px; }
    .content-grid { display: grid; }
    .notes-column { display: block; }
    .segment { page-break-inside: avoid; }
    .word-list { page-break-before: always; grid-column: 1 / -1; }
    .phrase-list { page-break-before: always; grid-column: 1 / -1; }
    .timestamp { color: #ccc; }
    .video-url { font-size: 6.5pt; }
    .legend { background: none; border: 1px solid #ddd; }
    .notes-header { color: #ddd; }
}

@media screen {
    .difficult-word:hover .annotation {
        background: #f0e6ff;
        border-radius: 3px;
        padding: 1px 4px;
    }
    .learn-phrase:hover .phrase-annotation {
        background: #d4edda;
        border-radius: 3px;
        padding: 1px 4px;
    }
}
"""


def generate_bilingual_html(
    video_info: dict,
    segments: list[dict],
    output_path: Path | None = None,
) -> str:
    title = html.escape(video_info.get("title", "未知标题"))
    author = html.escape(video_info.get("author", "未知"))
    duration = _format_duration(video_info.get("duration_seconds"))
    url = html.escape(video_info.get("url", ""))
    date = datetime.now().strftime("%Y-%m-%d %H:%M")

    segments_html_parts: list[str] = []
    all_annotations: list[dict] = []
    all_phrases: list[dict] = []

    is_chinese_source = False
    if segments:
        first_text = segments[0].get("text_original", "")
        chinese_chars = sum(1 for c in first_text if '一' <= c <= '鿿')
        is_chinese_source = chinese_chars > len(first_text) * 0.3

    for seg in segments:
        ts = _format_timestamp(seg.get("start_time"), seg.get("end_time"))
        original = seg.get("text_original", "")
        translated = seg.get("text_translated", "")
        annotations = seg.get("annotations", [])
        phrase_annotations = seg.get("phrase_annotations", [])

        ts_html = f'<span class="timestamp">{ts}</span>' if ts else ""

        parts = [f'<div class="segment">']
        if ts_html:
            parts.append(ts_html)

        if is_chinese_source:
            parts.append(f'<p class="english-line">{html.escape(original)}</p>')
            if translated:
                eng_annotated = _render_annotated_text(translated, annotations, phrase_annotations)
                parts.append(f'<p class="chinese-line">{eng_annotated}</p>')
        else:
            eng_text = _render_annotated_text(original, annotations, phrase_annotations)
            parts.append(f'<p class="english-line">{eng_text}</p>')
            if translated:
                parts.append(f'<p class="chinese-line">{html.escape(translated)}</p>')

        parts.append('</div>')
        segments_html_parts.append("\n".join(parts))

        for ann in annotations:
            all_annotations.append(ann)
        for pa in phrase_annotations:
            all_phrases.append(pa)

    unique_words: dict[str, dict] = {}
    for ann in all_annotations:
        w = ann.get("word", "").lower()
        if w not in unique_words:
            unique_words[w] = ann

    word_table_rows: list[str] = []
    for word, ann in sorted(unique_words.items(), key=lambda x: x[1].get("cefr_level", "zz")):
        cefr = ann.get("cefr_level", "Unknown")
        ipa = html.escape(ann.get("ipa", "—"))
        chinese = html.escape(ann.get("chinese_definition", "—"))
        badge_class = cefr.lower() if cefr.lower() in ("a1","a2","b1","b2","c1","c2") else "unknown"
        word_table_rows.append(
            f'<tr>'
            f'<td class="word-cell">{html.escape(word)} <span class="ipa-cell">/{ipa}/</span></td>'
            f'<td class="cefr-cell"><span class="cefr-badge {badge_class}">{html.escape(cefr)}</span></td>'
            f'<td>{chinese}</td>'
            f'</tr>'
        )

    word_list_html = ""
    if unique_words:
        word_list_html = f"""
        <div class="word-list">
            <h2 class="word-list-title">生词表 Difficult Words</h2>
            <table>
                <thead>
                    <tr><th>单词 / 音标</th><th>等级</th><th>中文释义</th></tr>
                </thead>
                <tbody>
                    {"".join(word_table_rows)}
                </tbody>
            </table>
            <div class="legend">
                <div class="legend-title">标注说明</div>
                <span class="legend-item"><span class="cefr-badge a1">A1</span> 入门</span>
                <span class="legend-item"><span class="cefr-badge a2">A2</span> 初级</span>
                <span class="legend-item"><span class="cefr-badge b1">B1</span> 中级</span>
                <span class="legend-item"><span class="cefr-badge b2">B2</span> 中高级</span>
                <span class="legend-item"><span class="cefr-badge c1">C1</span> 高级</span>
                <span class="legend-item"><span class="cefr-badge c2">C2</span> 精通</span>
                <span class="legend-phrase-item"><span class="legend-phrase-sample">短语</span> 值得学习的常用短语</span>
            </div>
        </div>
        """

    unique_phrases: dict[str, dict] = {}
    for pa in all_phrases:
        p = pa.get("phrase", "").lower()
        if p not in unique_phrases:
            unique_phrases[p] = pa

    phrase_table_rows: list[str] = []
    for phrase, pa in sorted(unique_phrases.items()):
        chinese = html.escape(pa.get("chinese_definition", ""))
        phrase_table_rows.append(
            f'<tr>'
            f'<td class="phrase-cell">{html.escape(phrase)}</td>'
            f'<td>{chinese}</td>'
            f'</tr>'
        )

    phrase_list_html = ""
    if unique_phrases:
        phrase_list_html = f"""
        <div class="phrase-list">
            <h2 class="phrase-list-title">常用短语 Useful Phrases</h2>
            <table>
                <thead>
                    <tr><th>短语</th><th>中文释义</th></tr>
                </thead>
                <tbody>
                    {"".join(phrase_table_rows)}
                </tbody>
            </table>
        </div>
        """

    notes_lines: list[str] = []
    notes_lines.append('<div class="notes-header">笔记 Notes</div>')
    for seg in segments:
        text_len = len(seg.get("text_original", ""))
        note_lines = max(2, min(4, text_len // 40 + 1))
        for _ in range(note_lines):
            notes_lines.append('<div class="notes-line">&nbsp;</div>')
        notes_lines.append('<div class="notes-divider">&nbsp;</div>')

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title id="doc-page-title">{title} - 双语字幕</title>
    <style>{_CSS}</style>
</head>
<body>
    <div class="document-header">
        <h1 class="video-title" id="doc-title">{title}</h1>
        <div class="video-meta">
            <span class="meta-item">作者: {author}</span>
            <span class="meta-item">时长: {duration}</span>
            <span class="meta-item">提取时间: {date}</span>
        </div>
        <div class="video-url" id="doc-url">来源: {url}</div>
    </div>

    <div class="content-grid">
        <div class="subtitle-column">
            {"".join(segments_html_parts)}
        </div>
        <div class="notes-column">
            {"".join(notes_lines)}
        </div>
    </div>

    {word_list_html}
    {phrase_list_html}
</body>
</html>"""

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_html, encoding="utf-8")

    return full_html
