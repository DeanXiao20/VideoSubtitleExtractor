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

.tab-nav {
    display: flex;
    border-bottom: 2px solid #2c3e50;
    margin-bottom: 14px;
}

.tab-btn {
    flex: 1;
    padding: 8px 16px;
    font-size: 10pt;
    font-weight: 600;
    text-align: center;
    cursor: pointer;
    border: none;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    background: #ecf0f1;
    color: #7f8c8d;
    font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
}

.tab-btn.active {
    background: #fff;
    color: #2c3e50;
    border-bottom-color: #2c3e50;
}

.tab-btn:not(.active):hover {
    background: #dfe6e9;
}

.tab-panel { display: none; }
.tab-panel.active { display: block; }

.summary-section {
    margin-bottom: 16px;
    padding: 10px 14px;
    background: #ebf5fb;
    border-left: 3px solid #3498db;
    border-radius: 0 4px 4px 0;
}

.summary-title {
    font-size: 11pt;
    font-weight: 700;
    color: #2c3e50;
    margin: 0 0 6px 0;
}

.summary-text {
    font-size: 9.5pt;
    line-height: 1.6;
    color: #333;
    margin: 0;
}

.classic-sentences-title {
    font-size: 11pt;
    font-weight: 700;
    color: #2c3e50;
    border-bottom: 2px solid #2c3e50;
    padding-bottom: 4px;
    margin: 0 0 10px 0;
}

.classic-sentence-item {
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 1px dotted #e8e8e8;
    page-break-inside: avoid;
}

.classic-sentence-item:last-child {
    border-bottom: none;
}

.classic-sentence-num {
    font-size: 8pt;
    color: #3498db;
    font-weight: 700;
    margin-right: 4px;
}

@media print {
    body { max-width: none; padding: 0; margin: 0; font-size: 9.5pt; }
    .document-header { border-bottom: 2px solid #2c3e50; padding-bottom: 6px; }
    .tab-nav { display: none; }
    .tab-panel { display: block !important; }
    .tab-panel:not(.active) { page-break-before: always; }
    .summary-section { background: none; border: 1px solid #bbb; }
    .content-grid { display: block; }
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

@media (max-width: 600px) {
    .tab-btn { font-size: 9pt; padding: 6px 8px; }
}
"""


_TAB_JS = """
(function() {
    var tabs = document.querySelectorAll('.tab-btn');
    var panels = document.querySelectorAll('.tab-panel');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].addEventListener('click', function() {
            for (var j = 0; j < tabs.length; j++) {
                tabs[j].classList.remove('active');
                panels[j].classList.remove('active');
            }
            this.classList.add('active');
            var target = this.getAttribute('data-tab');
            document.getElementById(target).classList.add('active');
        });
    }
})();
"""


def _build_word_list_html(unique_words: dict[str, dict], filter_set: set[str] | None = None) -> str:
    word_table_rows: list[str] = []
    for word, ann in sorted(unique_words.items(), key=lambda x: x[1].get("cefr_level", "zz")):
        if filter_set is not None and word not in filter_set:
            continue
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

    if not word_table_rows:
        return ""

    return f"""
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


def _build_phrase_list_html(unique_phrases: dict[str, dict], filter_set: set[str] | None = None) -> str:
    phrase_table_rows: list[str] = []
    for phrase, pa in sorted(unique_phrases.items()):
        if filter_set is not None and phrase not in filter_set:
            continue
        chinese = html.escape(pa.get("chinese_definition", ""))
        phrase_table_rows.append(
            f'<tr>'
            f'<td class="phrase-cell">{html.escape(phrase)}</td>'
            f'<td>{chinese}</td>'
            f'</tr>'
        )

    if not phrase_table_rows:
        return ""

    return f"""
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


def generate_bilingual_html(
    video_info: dict,
    segments: list[dict],
    output_path: Path | None = None,
    video_summary: str = "",
    classic_sentences: list[dict] | None = None,
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

    unique_phrases: dict[str, dict] = {}
    for pa in all_phrases:
        p = pa.get("phrase", "").lower()
        if p not in unique_phrases:
            unique_phrases[p] = pa

    # Collect words/phrases from classic sentences for overview filtering
    overview_word_set: set[str] = set()
    overview_phrase_set: set[str] = set()
    if classic_sentences:
        for seg in classic_sentences:
            for ann in seg.get("annotations", []):
                overview_word_set.add(ann.get("word", "").lower())
            for pa in seg.get("phrase_annotations", []):
                overview_phrase_set.add(pa.get("phrase", "").lower())

    has_overview = bool(classic_sentences)
    tab_nav_html = ""
    overview_panel_html = ""

    if has_overview:
        tab_nav_html = f"""
        <div class="tab-nav">
            <button class="tab-btn active" data-tab="tab-overview">概览 Overview</button>
            <button class="tab-btn" data-tab="tab-detail">详读 Detail</button>
        </div>
        """

        overview_parts: list[str] = []

        if video_summary:
            escaped_summary = html.escape(video_summary)
            overview_parts.append(f"""
            <div class="summary-section">
                <div class="summary-title">视频简介</div>
                <p class="summary-text">{escaped_summary}</p>
            </div>
            """)

        overview_parts.append('<div class="classic-sentences-title">经典句子 Classic Sentences</div>')

        for idx, seg in enumerate(classic_sentences, 1):
            orig = seg.get("text_original", "")
            trans = seg.get("text_translated", "")
            ann = seg.get("annotations", [])
            phr = seg.get("phrase_annotations", [])
            ts = _format_timestamp(seg.get("start_time"), seg.get("end_time"))

            if is_chinese_source:
                eng_html = html.escape(orig)
                cn_html = _render_annotated_text(trans, ann, phr) if trans else ""
            else:
                eng_html = _render_annotated_text(orig, ann, phr)
                cn_html = html.escape(trans) if trans else ""

            ts_span = f'<span class="timestamp">{ts}</span> ' if ts else ""

            overview_parts.append(f"""
            <div class="classic-sentence-item">
                <span class="classic-sentence-num">{idx}.</span>{ts_span}
                <p class="english-line">{eng_html}</p>
                {"<p class='chinese-line'>" + cn_html + "</p>" if cn_html else ""}
            </div>
            """)

        # Overview word/phrase lists: only words from classic sentences
        overview_word_html = _build_word_list_html(unique_words, overview_word_set)
        overview_phrase_html = _build_phrase_list_html(unique_phrases, overview_phrase_set)

        overview_panel_html = f"""
        <div class="tab-panel active" id="tab-overview">
            {"".join(overview_parts)}
            {overview_word_html}
            {overview_phrase_html}
        </div>
        """

    # Detail word/phrase lists: all words
    detail_word_html = _build_word_list_html(unique_words)
    detail_phrase_html = _build_phrase_list_html(unique_phrases)

    if has_overview:
        detail_panel_html = f"""
        <div class="tab-panel" id="tab-detail">
            {"".join(segments_html_parts)}
            {detail_word_html}
            {detail_phrase_html}
        </div>
        """
    else:
        detail_panel_html = f"""
        {"".join(segments_html_parts)}
        {detail_word_html}
        {detail_phrase_html}
        """

    script_html = f"<script>{_TAB_JS}</script>" if has_overview else ""

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

    {tab_nav_html}
    {overview_panel_html}
    {detail_panel_html}
    {script_html}
</body>
</html>"""

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_html, encoding="utf-8")

    return full_html
