import json
import logging
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    SETTINGS_PATH,
    DATA_DIR,
    OUTPUT_DIR,
    CEFR_DIFFICULTY_THRESHOLD,
    WHISPER_MODEL_SIZE,
    APP_PORT,
    MAX_CLASSIC_SENTENCES,
)
from src.pipeline import PipelineResult

_logger = logging.getLogger(__name__)


_HISTORY_CSS = """
<style>
.vid-extras-badge {
    display: inline-block;
    font-size: 11px;
    color: #64748b;
    background: #f1f5f9;
    padding: 2px 8px;
    border-radius: 10px;
    margin-right: 4px;
}
</style>
"""


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_cloudflare_url(filename: str) -> str:
    """Return the public Cloudflare Pages URL for a file, or empty if not configured."""
    from config import CF_PAGES_PROJECT

    if not CF_PAGES_PROJECT:
        return ""
    return f"https://{CF_PAGES_PROJECT}.pages.dev/{filename}"


def _get_share_url(filename: str) -> str:
    """Prefer Cloudflare Pages URL, then frp URL, then local IP."""
    cf_url = _get_cloudflare_url(filename)
    if cf_url:
        return cf_url
    from src.share_server import _get_local_ip

    frp_url = (
        st.session_state.settings.get("frp_public_url", "")
        if "settings" in st.session_state
        else ""
    )
    if frp_url:
        base = frp_url.rstrip("/")
        return f"{base}/output/{filename}"
    ip = _get_local_ip()
    return f"http://{ip}:{APP_PORT}/output/{filename}"


def _reannotate_in_place(
    vid: int,
    segments: list[dict],
    settings: dict,
    db,
    progress_cb=None,
) -> list[dict]:
    """Annotate segments fresh, save to DB, return updated segments. Pure logic, no Streamlit UI."""
    from src.annotator import annotate_all_segments
    from src.cefr_loader import CEFRLookup
    from src.translator import Translator

    for seg in segments:
        seg.pop("annotations", None)
        seg.pop("phrase_annotations", None)

    cefr_lookup = CEFRLookup()
    proxy_str = (
        settings.get("https_proxy")
        or settings.get("http_proxy")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
    )
    proxy_dict = {"http": proxy_str, "https": proxy_str} if proxy_str else None
    translator = Translator(proxy=proxy_dict)
    threshold = settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD)

    annotated = annotate_all_segments(
        segments,
        cefr_lookup,
        translator,
        threshold=threshold,
        progress_callback=progress_cb,
    )
    db.save_segments(vid, annotated)

    # Also refresh the cached classic_sentences snapshot so the overview tab
    # reflects the latest annotations (it stores its own copy at extraction time).
    extras = db.get_video_extras(vid)
    classic = extras.get("classic_sentences") or []
    if classic:
        by_start: dict = {}
        by_text: dict = {}
        for seg in annotated:
            st_v = seg.get("start_time")
            if st_v is not None:
                by_start[round(float(st_v), 2)] = seg
            txt = (seg.get("text_original") or "").strip()
            if txt:
                by_text.setdefault(txt, seg)

        refreshed: list[dict] = []
        for cs in classic:
            match = None
            st_v = cs.get("start_time")
            if st_v is not None:
                match = by_start.get(round(float(st_v), 2))
            if match is None:
                match = by_text.get((cs.get("text_original") or "").strip())
            new_cs = dict(cs)
            if match is not None:
                new_cs["annotations"] = match.get("annotations", [])
                new_cs["phrase_annotations"] = match.get("phrase_annotations", [])
            refreshed.append(new_cs)
        db.save_video_extras(vid, extras.get("video_summary", ""), refreshed)

    try:
        db.conn.commit()
        db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass
    return annotated


def regenerate_html_with_extras(
    video: dict, settings: dict, db=None
) -> tuple[int, bool]:
    from src.sentence_extractor import extract_extras
    from src.html_generator import generate_bilingual_html
    from src.database import Database

    own_db = db is None
    if own_db:
        db = Database()

    try:
        vid = video.get("id")
        if vid is None:
            st.error("视频信息缺少ID，无法提取")
            return 0, False

        segments = db.get_segments(vid)

        status_steps = {}

        def progress_callback(stage: str, fraction: float, text: str):
            if stage == "extract" and status_steps:
                status_steps["text"].markdown(f"**{text}**")
                status_steps["bar"].progress(min(fraction, 1.0))

        with st.status("正在提取简介和经典句子...", expanded=True) as status:
            status_steps = {
                "text": st.empty(),
                "bar": st.progress(0),
            }
            try:
                summary, classic, used_llm = extract_extras(
                    segments,
                    video,
                    settings,
                    max_count=MAX_CLASSIC_SENTENCES,
                    progress_callback=progress_callback,
                )
            except RuntimeError as e:
                status.update(label="提取失败", state="error", expanded=False)
                st.error(str(e))
                return 0, False

            status_steps["text"].markdown("**正在保存...**")
            status_steps["bar"].progress(0.9)

        # Save to DB OUTSIDE the st.status block to avoid widget-related interruptions
        _logger.info(
            f"Saving extras for vid={vid}: {len(classic)} classic sentences, summary={bool(summary)}"
        )
        db.save_video_extras(vid, summary, classic)
        try:
            db.conn.commit()
            db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass
        _logger.info(f"Extras saved and checkpointed for vid={vid}")

        # Re-annotate so the regenerated HTML reflects the latest phrase dictionary
        annotated = segments
        if segments:
            with st.status("正在重新标注...", expanded=True) as ann_status:
                ann_text = st.empty()
                ann_bar = st.progress(0)

                def ann_cb(fraction: float, text: str):
                    ann_text.markdown(f"**{text}**")
                    ann_bar.progress(min(fraction, 1.0))

                try:
                    annotated = _reannotate_in_place(
                        vid, segments, settings, db, progress_cb=ann_cb
                    )
                    ann_text.markdown("**完成**")
                    ann_bar.progress(1.0)
                    ann_status.update(
                        label="重新标注完成", state="complete", expanded=False
                    )
                except Exception as e:
                    _logger.exception(
                        "Re-annotation failed during regenerate_html_with_extras"
                    )
                    ann_status.update(
                        label="标注失败（仍会重新生成HTML）",
                        state="error",
                        expanded=False,
                    )
                    st.warning(f"标注失败：{e}，将使用旧标注重新生成 HTML")

        html_filename = db.get_html_filename(vid)
        if not html_filename:
            from src.utils import slugify_title

            html_filename = f"{slugify_title(video.get('title', ''), video.get('video_id', 'output'))}.html"
            db.save_html_filename(vid, html_filename)
        output_path = OUTPUT_DIR / html_filename
        generate_bilingual_html(video, annotated, output_path, summary, classic)

        # Update pipeline_result in session_state so preview shows fresh HTML
        if (
            st.session_state.get("pipeline_result")
            and st.session_state.pipeline_result.video_id == vid
        ):
            if output_path.exists():
                new_html = output_path.read_text(encoding="utf-8")
                result = st.session_state.pipeline_result
                st.session_state.pipeline_result = PipelineResult(
                    html=new_html,
                    video_info=result.video_info,
                    segments=annotated,
                    video_id=result.video_id,
                    html_filename=result.html_filename,
                    resumed=result.resumed,
                )

        # Also store extras in session_state for recovery after rerun
        st.session_state["_pending_extras"] = {
            "vid": vid,
            "summary": summary,
            "classic": classic,
            "html_filename": html_filename,
            "saved": True,
        }

        return len(classic), used_llm
    finally:
        if own_db:
            db.close()


def reannotate_video(video: dict, settings: dict, db=None) -> int:
    """Re-run word/phrase annotation on cached bilingual segments and regenerate HTML.

    Skips video download, transcription, and translation. Uses whatever is currently
    in the DB (text_original + text_translated) and rebuilds annotations from scratch
    with the current phrase dictionary and CEFR settings.

    Returns the total number of phrase annotations in the refreshed HTML.
    """
    from src.html_generator import generate_bilingual_html
    from src.database import Database

    own_db = db is None
    if own_db:
        db = Database()
    try:
        vid = video.get("id")
        if vid is None:
            st.error("视频信息缺少ID，无法重新标注")
            return 0

        segments = db.get_segments(vid)
        if not segments:
            st.warning("此视频暂无字幕数据，无法重新标注")
            return 0

        with st.status("正在重新标注（不会重新抽取视频）...", expanded=True) as status:
            text_slot = st.empty()
            bar = st.progress(0)

            def cb(fraction: float, text: str):
                text_slot.markdown(f"**{text}**")
                bar.progress(min(fraction, 1.0))

            annotated = _reannotate_in_place(
                vid, segments, settings, db, progress_cb=cb
            )

            text_slot.markdown("**正在生成HTML...**")
            bar.progress(0.95)

            html_filename = db.get_html_filename(vid)
            if not html_filename:
                from src.utils import slugify_title

                html_filename = f"{slugify_title(video.get('title', ''), video.get('video_id', 'output'))}.html"
                db.save_html_filename(vid, html_filename)
            output_path = OUTPUT_DIR / html_filename

            extras = db.get_video_extras(vid)
            summary = extras.get("video_summary", "")
            classic = extras.get("classic_sentences", [])
            generate_bilingual_html(video, annotated, output_path, summary, classic)

            text_slot.markdown("**完成**")
            bar.progress(1.0)
            status.update(label="重新标注完成", state="complete", expanded=False)

        # Sync session_state preview if this video is currently shown
        if (
            st.session_state.get("pipeline_result")
            and st.session_state.pipeline_result.video_id == vid
            and output_path.exists()
        ):
            new_html = output_path.read_text(encoding="utf-8")
            r = st.session_state.pipeline_result
            st.session_state.pipeline_result = PipelineResult(
                html=new_html,
                video_info=r.video_info,
                segments=annotated,
                video_id=r.video_id,
                html_filename=r.html_filename,
                resumed=r.resumed,
            )

        return sum(len(s.get("phrase_annotations", [])) for s in annotated)
    finally:
        if own_db:
            db.close()


def init_session_state() -> None:
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    if "pipeline_result" not in st.session_state:
        st.session_state.pipeline_result = None


def render_generate_tab() -> None:
    st.markdown("### 输入视频链接")
    url = st.text_input(
        "视频URL",
        placeholder="粘贴 YouTube / Bilibili 视频链接...",
        label_visibility="collapsed",
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        prefer_subs = st.checkbox("优先使用已有字幕", value=True)
    with col2:
        force_transcribe = st.checkbox("强制转录", value=False)
    with col3:
        model_display = {
            "tiny": "Tiny (75MB, 快速)",
            "base": "Base (145MB)",
            "small": "Small (488MB, 推荐)",
            "medium": "Medium (1.5GB)",
            "large-v3": "Large-v3 (3GB, 最准确)",
        }
        current_model = st.session_state.settings.get(
            "whisper_model", WHISPER_MODEL_SIZE
        )
        model_choice = st.selectbox(
            "Whisper模型",
            options=list(model_display.keys()),
            format_func=lambda x: model_display.get(x, x),
            index=(
                list(model_display.keys()).index(current_model)
                if current_model in model_display
                else 2
            ),
        )
    with col4:
        force_rerun = st.checkbox(
            "从头开始", value=False, help="忽略已有进度，重新处理所有步骤"
        )

    # Check for existing progress
    if url.strip() and not force_rerun:
        from src.database import Database

        _chk_db = Database()
        _existing = _chk_db.get_video_by_url(url.strip())
        if _existing:
            _pstage = _existing.get("pipeline_stage", "")
            if _pstage and _pstage != "completed":
                stage_names = {
                    "extracted": "视频信息提取",
                    "transcribed": "字幕/转录",
                    "translated": "翻译",
                    "annotated": "生词标注",
                }
                _stage_label = stage_names.get(_pstage, _pstage)
                st.info(
                    f"检测到上次处理中断于【{_stage_label}】阶段，将从断点继续。勾选「从头开始」可重新处理。"
                )
        _chk_db.close()

    generate_clicked = st.button(
        "一键生成双语字幕", type="primary", use_container_width=True
    )

    if generate_clicked:
        if not url.strip():
            st.error("请输入视频链接")
            return

        st.session_state.pipeline_result = None

        with st.status("正在处理...", expanded=True) as status:
            stages = {
                "extract": st.empty(),
                "subtitles": st.empty(),
                "transcribe": st.empty(),
                "translate": st.empty(),
                "annotate": st.empty(),
                "generate": st.empty(),
                "save": st.empty(),
            }
            progress_bars = {k: st.progress(0) for k in stages}

            def progress_callback(stage: str, fraction: float, text: str):
                if stage in stages:
                    stages[stage].markdown(f"**{text}**")
                    progress_bars[stage].progress(min(fraction, 1.0))

            try:
                from src.pipeline import Pipeline

                settings = dict(st.session_state.settings)
                settings["whisper_model"] = model_choice
                pipeline = Pipeline(settings=settings)

                result = pipeline.run(
                    url.strip(),
                    force_transcribe=force_transcribe,
                    prefer_subtitles=prefer_subs,
                    force=force_rerun,
                    progress_callback=progress_callback,
                )

                st.session_state.pipeline_result = result
                status.update(label="处理完成!", state="complete", expanded=False)

            except Exception as e:
                import logging

                logging.getLogger(__name__).exception("Pipeline failed")
                status.update(label="处理失败", state="error", expanded=True)
                msg = str(e)
                if "不存在" in msg or "删除" in msg:
                    st.error("视频不存在或已被删除，请检查链接")
                elif "网络" in msg or "连接" in msg or "timeout" in msg.lower():
                    st.error("网络连接失败，请检查网络或在设置中配置代理")
                elif "私密" in msg or "登录" in msg:
                    st.error("视频为私密视频或需要登录，无法访问")
                else:
                    st.error("处理失败，请检查视频链接是否有效或稍后重试")
                return

    result = st.session_state.pipeline_result
    if result is None:
        return

    st.divider()

    # Video info card
    info = result.video_info
    with st.container(border=True):
        st.markdown(f"### {info.get('title', '未知标题')}")
        meta_cols = st.columns(4)
        meta_cols[0].markdown(f"**作者:** {info.get('author', '未知')}")
        duration_s = info.get("duration_seconds", 0)
        m, s = divmod(int(duration_s or 0), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        meta_cols[1].markdown(f"**时长:** {dur_str}")
        meta_cols[2].markdown(f"**平台:** {info.get('platform', '未知')}")
        meta_cols[3].markdown(f"**字幕条数:** {len(result.segments)}")
        # Show extras status
        _vid_info = info.get("id") or result.video_id
        if _vid_info:
            from src.database import Database as _DB2

            _db2 = _DB2()
            _extras = _db2.get_video_extras(_vid_info)
            _db2.close()
            if _extras.get("video_summary"):
                st.caption(f"简介: {_extras['video_summary'][:80]}...")
            if _extras.get("classic_sentences"):
                st.caption(f"已提取 {len(_extras['classic_sentences'])} 句经典句子")
        st.caption(f"来源: {info.get('url', '')}")
        video_url = info.get("url", "")
        if video_url and video_url.startswith("http"):
            st.link_button("▶ 打开原视频", video_url)

    # Action buttons
    btn_col1, btn_col2, btn_col3, btn_col4, btn_col5, btn_col6 = st.columns(6)
    with btn_col1:
        if st.button("🖨️ 打印", use_container_width=True):
            st.components.v1.html(
                "<script>window.print();</script>",
                height=0,
            )
    with btn_col2:
        video_id = info.get("video_id", "output")
        st.download_button(
            "📥 下载HTML",
            data=result.html.encode("utf-8"),
            file_name=f"{video_id}_bilingual.html",
            mime="text/html",
            use_container_width=True,
        )
    with btn_col3:
        difficult_count = sum(1 for seg in result.segments if seg.get("annotations"))
        phrase_count = sum(
            1 for seg in result.segments if seg.get("phrase_annotations")
        )
        info_text = f"生词: {difficult_count}段"
        if phrase_count:
            info_text += f" | 短语: {phrase_count}段"
        st.info(info_text)
    with btn_col4:
        # Check if extras already exist
        _has_extras = False
        _vid_for_extras = info.get("id") or result.video_id
        if _vid_for_extras:
            from src.database import Database as _DB

            _chk_db = _DB()
            _chk_extras = _chk_db.get_video_extras(_vid_for_extras)
            _chk_db.close()
            _has_extras = bool(
                _chk_extras.get("video_summary") or _chk_extras.get("classic_sentences")
            )
        _extract_label = "重新提取" if _has_extras else "提取简介和经典句子"
        if st.button(_extract_label, use_container_width=True, key="gen_extract"):
            count, used_llm = regenerate_html_with_extras(
                info, dict(st.session_state.settings)
            )
            if count > 0:
                st.success(f"已提取简介和 {count} 句经典句子")
                st.rerun()
    with btn_col5:
        if st.button("发布", use_container_width=True, key="gen_publish"):
            from src.publish import publish_single
            from src.share_server import generate_qr_code

            publish_fn = (
                result.html_filename or f"{info.get('video_id', 'output')}.html"
            )
            pub_result = publish_single(publish_fn)
            if pub_result["success"]:
                pub_url = pub_result["url"] or f"https://subtitle-docs.pages.dev"
                st.session_state["_publish_url"] = pub_url
                st.session_state["_publish_qr"] = generate_qr_code(pub_url)
                st.success("已发布到线上")
            else:
                st.error(f"发布失败: {pub_result['error']}")
    with btn_col6:
        if st.button("📱 微信分享", use_container_width=True):
            from src.share_server import generate_qr_code

            filename = result.html_filename or f"{info.get('video_id', 'output')}.html"
            share_url = _get_share_url(filename)
            qr_bytes = generate_qr_code(share_url)
            st.session_state._share_url = share_url
            st.session_state._share_qr = qr_bytes

    # QR code display
    if st.session_state.get("_share_qr"):
        share_url = st.session_state.get("_share_url", "")
        qr_bytes = st.session_state["_share_qr"]
        with st.container(border=True):
            qr_col, url_col = st.columns([1, 2])
            with qr_col:
                st.image(qr_bytes, caption="微信扫码查看", width=200)
            with url_col:
                st.markdown("**微信扫码或复制链接打开**")
                st.code(share_url, language=None)
                st.caption(
                    "同一WiFi下直接扫码。外网访问需配置frp，见设置页。如无法访问，请以管理员身份运行cmd开放端口: netsh advfirewall firewall add rule name=Share dir=in action=allow protocol=tcp localport="
                    + str(APP_PORT)
                )

    # Publish result display
    if st.session_state.get("_publish_qr"):
        pub_url = st.session_state.get("_publish_url", "")
        pub_qr = st.session_state["_publish_qr"]
        with st.container(border=True):
            qr_col, url_col = st.columns([1, 2])
            with qr_col:
                st.image(pub_qr, caption="扫码查看", width=200)
            with url_col:
                st.markdown("**发布成功！扫码或点击链接查看**")
                st.link_button("打开网页", pub_url)
                st.code(pub_url, language=None)

    # HTML preview
    st.markdown("### 预览")
    with st.container(height=600):
        st.components.v1.html(result.html, height=600, scrolling=True)


@st.dialog("双语字幕预览", width="large")
def show_preview_dialog(video: dict, segments: list[dict]):
    from src.annotator import _load_phrase_dict, _find_phrases
    from src.database import Database
    from src.html_generator import generate_bilingual_html

    phrase_dict = _load_phrase_dict()
    enriched: list[dict] = []
    for seg in segments:
        s = dict(seg)
        if not s.get("phrase_annotations"):
            text = s.get("text_original", "")
            s["phrase_annotations"] = [
                {
                    "type": "phrase",
                    "phrase": p.phrase,
                    "chinese_definition": p.chinese_definition,
                    "start_pos": p.start_pos,
                    "end_pos": p.end_pos,
                }
                for p in _find_phrases(text, phrase_dict)
            ]
        enriched.append(s)
    segments = enriched

    html_content = generate_bilingual_html(
        video, segments, video_summary="", classic_sentences=None
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "📥 下载HTML",
            data=html_content.encode("utf-8"),
            file_name=f"{video.get('video_id', 'output')}_bilingual.html",
            mime="text/html",
            use_container_width=True,
        )
    with col2:
        if st.button("🖨️ 打印", use_container_width=True):
            st.components.v1.html("<script>window.print();</script>", height=0)
    with col3:
        if st.button("📱 微信分享"):
            from src.share_server import generate_qr_code

            _db = Database()
            filename = (
                _db.get_html_filename(video.get("id", 0))
                or f"{video.get('video_id', 'output')}.html"
            )
            _db.close()
            share_url = _get_share_url(filename)
            qr_bytes = generate_qr_code(share_url)
            st.session_state._share_url = share_url
            st.session_state._share_qr = qr_bytes

    if st.session_state.get("_share_qr"):
        share_url = st.session_state.get("_share_url", "")
        qr_bytes = st.session_state["_share_qr"]
        with st.container(border=True):
            qr_col, url_col = st.columns([1, 2])
            with qr_col:
                st.image(qr_bytes, caption="微信扫码查看", width=180)
            with url_col:
                st.markdown("**微信扫码或复制链接打开**")
                st.code(share_url, language=None)
                st.caption(
                    "手机需与电脑在同一WiFi网络下。如无法访问，请以管理员身份运行cmd: netsh advfirewall firewall add rule name=Share dir=in action=allow protocol=tcp localport="
                    + str(APP_PORT)
                )

    with st.container(height=800):
        st.components.v1.html(html_content, height=800, scrolling=True)


@st.dialog("确认删除")
def confirm_delete_dialog(db, video_id: int, title: str):
    st.warning(f"确定要删除「{title}」吗？此操作不可撤销。")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("取消", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("确认删除", type="primary", use_container_width=True):
            db.delete_video(video_id)
            st.success("已删除")
            st.rerun()


@st.dialog("编辑视频信息")
def edit_video_dialog(db, video: dict):
    new_title = st.text_input("标题", value=video.get("title", ""))
    new_url = st.text_input("视频链接", value=video.get("url", ""))
    col1, col2 = st.columns(2)
    with col1:
        if st.button("取消", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("保存", type="primary", use_container_width=True):
            db.update_video(video["id"], title=new_title, url=new_url)
            st.success("已保存")
            st.rerun()


def _video_meta(db, video: dict) -> dict:
    vid = video["id"]
    pstage = video.get("pipeline_stage", "")
    is_incomplete = bool(pstage) and pstage != "completed"
    html_fn = db.get_html_filename(vid) or f"{video.get('video_id', '')}.html"
    html_path = OUTPUT_DIR / html_fn
    cf_url = _get_cloudflare_url(html_fn) if html_path.exists() else ""
    extras = db.get_video_extras(vid)
    duration_s = video.get("duration_seconds", 0)
    m, s = divmod(int(duration_s or 0), 60)
    h, m = divmod(m, 60)
    dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    has_summary = bool(extras.get("video_summary"))
    has_classic = bool(extras.get("classic_sentences"))
    classic_count = len(extras.get("classic_sentences", []))
    is_published = bool(cf_url)
    return {
        "vid": vid,
        "title": video.get("title", "未知标题"),
        "dur_str": dur_str,
        "pstage": pstage,
        "is_incomplete": is_incomplete,
        "html_path": html_path,
        "html_fn": html_fn,
        "cf_url": cf_url,
        "has_summary": has_summary,
        "has_classic": has_classic,
        "classic_count": classic_count,
        "is_published": is_published,
        "updated_date": (video.get("updated_at") or "")[:10],
        "video_url": video.get("url", ""),
        "author": video.get("author", ""),
        "platform": video.get("platform", ""),
    }


def _render_actions_popover(m: dict, video: dict, db) -> None:
    vid = m["vid"]
    with st.popover("⋯", help="更多操作"):
        if m["cf_url"]:
            st.link_button("🌐 在线查看", m["cf_url"], use_container_width=True)
        if st.button(
            "🔄 重新标注",
            key=f"reannot_{vid}",
            use_container_width=True,
            help="使用最新词典重新标注，不会重新抽取视频",
        ):
            n = reannotate_video(video, dict(st.session_state.settings), db=db)
            if n is not None:
                st.success(f"已重新标注，共 {n} 个短语")
                st.rerun()
        if st.button(
            "✨ 提取简介与经典句",
            key=f"extract_{vid}",
            use_container_width=True,
            help="提取简介与经典句子，并同步重新标注",
        ):
            count, _used = regenerate_html_with_extras(
                video, dict(st.session_state.settings), db=db
            )
            if count > 0:
                st.success(f"已提取简介和 {count} 句经典句子")
                st.rerun()
        st.divider()
        if st.button("✏️ 编辑", key=f"edit_{vid}", use_container_width=True):
            edit_video_dialog(db, video)
        if st.button("🗑️ 删除", key=f"del_{vid}", use_container_width=True):
            confirm_delete_dialog(db, vid, m["title"])


def _stage_label(pstage: str) -> str:
    names = {
        "extracted": "视频信息提取",
        "transcribed": "字幕/转录",
        "translated": "翻译",
        "annotated": "生词标注",
    }
    return names.get(pstage, pstage)


def _extras_caption(m: dict) -> str:
    parts = []
    if m["has_summary"]:
        parts.append("简介")
    if m["has_classic"]:
        parts.append(f"{m['classic_count']}句经典句")
    return " · ".join(parts)


def _meta_line(m: dict) -> str:
    parts = [
        m["author"] or "未知",
        m["dur_str"],
        m["platform"] or "未知",
        m["updated_date"],
    ]
    if m["is_incomplete"]:
        parts.append(f"⏸ {_stage_label(m['pstage'])}")
    return " · ".join(parts)


def _strip_status_class(m: dict) -> str:
    """Return CSS class for the sidebar strip based on video status."""
    if m["is_incomplete"]:
        return "status-incomplete"
    if m["is_published"]:
        return "status-published"
    return "status-completed"


def _render_video_card(m: dict, video: dict, db, index: int) -> None:
    status_cls = _strip_status_class(m)
    border_colors = {
        "status-completed": "#2563eb",
        "status-published": "#16a34a",
        "status-incomplete": "#ea580c",
    }
    border_color = border_colors.get(status_cls, "#2563eb")

    title = m["title"]
    video_url = m["video_url"]
    meta = _meta_line(m)
    extras = _extras_caption(m)
    caption_parts = [meta]
    if extras:
        caption_parts.append(extras)
    meta_str = " · ".join(caption_parts)

    link_html = ""
    if video_url and video_url.startswith("http"):
        link_html = (
            f'<div style="margin-top:2px;">'
            f'<a href="{video_url}" target="_blank" '
            f'style="font-size:12px;color:#2563eb;text-decoration:none;">'
            f"{video_url} ↗</a></div>"
        )

    # Left: info with border-left strip | Right: action buttons
    c_body, c_b1, c_b2, c_b3 = st.columns([6.5, 1, 1, 0.7])
    with c_body:
        st.markdown(
            f'<div style="border-left:4px solid {border_color};'
            f'padding:6px 16px;margin-bottom:2px;">'
            f'<div style="font-weight:700;font-size:16px;">{title}</div>'
            f'<div style="font-size:12px;color:#666;margin-top:2px;">{meta_str}</div>'
            f"{link_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c_b1:
        if m["html_path"].exists():
            st.link_button(
                "📄 打开", f"/output/{m['html_fn']}", use_container_width=True
            )
        elif m["cf_url"]:
            st.link_button("📄 在线", m["cf_url"], use_container_width=True)
        else:
            st.button(
                "📄", key=f"noview_{m['vid']}", disabled=True, use_container_width=True
            )
    with c_b2:
        if m["is_incomplete"]:
            if st.button(
                "▶ 继续",
                key=f"resume_{m['vid']}",
                use_container_width=True,
                help="从断点继续处理",
            ):
                db.close()
                from src.pipeline import Pipeline

                _pipeline = Pipeline(settings=dict(st.session_state.settings))
                try:
                    _pipeline.run(
                        video.get("url", ""),
                        prefer_subtitles=True,
                        progress_callback=lambda s, f, t: None,
                    )
                    st.success("处理完成!")
                    st.rerun()
                except Exception as e:
                    st.error(f"继续处理失败: {e}")
        else:
            if st.button(
                "🚀 发布",
                key=f"publish_{m['vid']}",
                use_container_width=True,
                help="发布到 Cloudflare Pages",
            ):
                from src.publish import publish_single

                pub_result = publish_single(m["html_fn"])
                if pub_result["success"]:
                    pub_url = (
                        pub_result["url"]
                        or _get_cloudflare_url(m["html_fn"])
                        or "https://subtitle-docs.pages.dev"
                    )
                    st.success("已发布到线上")
                    st.link_button("打开网页", pub_url)
                    st.code(pub_url, language=None)
                    st.session_state.pop("_cf_index_qr", None)
                else:
                    st.error(f"发布失败: {pub_result['error']}")
    with c_b3:
        _render_actions_popover(m, video, db)

    # Spacing between cards
    st.markdown('<div style="height:2px;"></div>', unsafe_allow_html=True)


def render_history_tab() -> None:
    from src.database import Database

    db = Database()

    _pending = st.session_state.pop("_pending_extras", None)
    if _pending and _pending.get("saved"):
        pass

    try:
        search = st.text_input("搜索视频标题...", placeholder="输入关键词")

        # Inject CSS
        st.markdown(_HISTORY_CSS, unsafe_allow_html=True)

        # Color legend
        st.caption("🔵 已完成  ·  🟢 已发布  ·  🟠 处理中")

        # Cloudflare index page QR + URL
        index_url = _get_cloudflare_url("index.html")
        if index_url:
            root_url = index_url.rsplit("/", 1)[0] + "/"
            with st.container(border=True):
                qr_col, info_col = st.columns([1, 3])
                with qr_col:
                    if "_cf_index_qr" not in st.session_state:
                        try:
                            from src.share_server import generate_qr_code

                            st.session_state["_cf_index_qr"] = generate_qr_code(
                                root_url
                            )
                        except Exception:
                            st.session_state["_cf_index_qr"] = None
                    qr_bytes = st.session_state.get("_cf_index_qr")
                    if qr_bytes:
                        st.image(qr_bytes, caption="扫码访问", width=140)
                with info_col:
                    st.markdown("**🌐 在线视频列表**")
                    st.caption("任何设备查看所有已发布的视频。")
                    st.link_button("打开在线列表", root_url)
                    st.code(root_url, language=None)

        videos = db.get_recent_videos(limit=50)
        if search.strip():
            videos = [
                v for v in videos if search.lower() in (v.get("title") or "").lower()
            ]

        if not videos:
            st.info("暂无处理记录")
            return

        for i, video in enumerate(videos, 1):
            m = _video_meta(db, video)
            _render_video_card(m, video, db, index=i)
    finally:
        db.close()


def render_settings_tab() -> None:
    settings = st.session_state.settings

    st.markdown("### 代理配置")
    settings["http_proxy"] = st.text_input(
        "HTTP代理",
        value=settings.get("http_proxy", os.getenv("HTTP_PROXY", "")),
        placeholder="http://127.0.0.1:7890",
    )
    settings["https_proxy"] = st.text_input(
        "HTTPS代理",
        value=settings.get("https_proxy", os.getenv("HTTPS_PROXY", "")),
        placeholder="http://127.0.0.1:7890",
    )

    st.markdown("### 转录配置")
    model_display = {
        "tiny": "Tiny (75MB, 快速)",
        "base": "Base (145MB)",
        "small": "Small (488MB, 推荐)",
        "medium": "Medium (1.5GB)",
        "large-v3": "Large-v3 (3GB, 最准确)",
    }
    settings["whisper_model"] = st.selectbox(
        "Whisper模型大小",
        options=list(model_display.keys()),
        format_func=lambda x: model_display.get(x, x),
        index=(
            list(model_display.keys()).index(
                settings.get("whisper_model", WHISPER_MODEL_SIZE)
            )
            if settings.get("whisper_model", WHISPER_MODEL_SIZE) in model_display
            else 2
        ),
    )

    settings["use_hf_mirror"] = st.checkbox(
        "使用HuggingFace镜像 (hf-mirror.com)",
        value=settings.get("use_hf_mirror", True),
        help="中国大陆用户建议开启，加速模型下载",
    )

    st.markdown("### 生词标注配置")
    threshold_display = {
        "A1": "A1 - 仅A1词汇为已知，其余标注 (初一上学期)",
        "A2": "A2 - A1-A2词汇为已知 (初一下学期)",
        "B1": "B1 - 标注中高级及以上 (较少标注)",
        "B2": "B2 - 仅标注高级词汇 (最少标注)",
    }
    settings["difficulty_threshold"] = st.selectbox(
        "生词难度阈值",
        options=list(threshold_display.keys()),
        format_func=lambda x: threshold_display.get(x, x),
        index=(
            list(threshold_display.keys()).index(
                settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD)
            )
            if settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD)
            in threshold_display
            else 0
        ),
        help="初一上学期对应A1，初一下学期对应A2，建议根据实际水平选择",
    )

    if st.button("保存设置", type="primary", use_container_width=True):
        st.session_state.settings = settings
        save_settings(settings)
        st.success("设置已保存")

    st.markdown("### AI 摘要配置")
    st.caption("配置后提取经典句子和视频简介时使用 LLM，未配置则使用规则评分。")
    from config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL

    key_status = "已配置" if LLM_API_KEY else "未配置"
    st.markdown(f"- **API Key:** {key_status}")
    st.markdown(f"- **API Base:** `{LLM_API_BASE}`")
    st.markdown(f"- **模型:** `{LLM_MODEL}`")
    st.caption(
        "编辑项目根目录 `.env` 文件修改配置（LLM_API_KEY / LLM_API_BASE / LLM_MODEL）"
    )

    st.markdown("### 微信分享 / frp 外网配置")
    st.caption(f"局域网分享无需配置。外网分享需 frp 将本地 {APP_PORT} 端口映射到公网。")

    frp_public_url = st.text_input(
        "frp 外网访问地址",
        value=settings.get("frp_public_url", ""),
        placeholder="http://your-domain.com:18765",
        help=f"frp 映射后的公网地址，如 http://sub.example.com 或 http://1.2.3.4:18765，本地端口为 {APP_PORT}",
    )
    settings["frp_public_url"] = frp_public_url

    with st.expander("查看 frpc.toml 配置参考"):
        from src.share_server import generate_frpc_config

        config_str = generate_frpc_config(frp_public_url or "http://your-server.com")
        st.code(config_str, language="toml")
        st.caption(
            f"将此配置保存为 frpc.toml，运行 `frpc -c frpc.toml` 即可。本地服务端口为 {APP_PORT}。"
        )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_session_state()

    st.set_page_config(
        page_title="视频字幕提取器",
        page_icon="🎬",
        layout="wide",
    )

    st.title("🎬 视频双语字幕提取器")
    st.caption("输入视频链接 → 一键生成中英双语文稿 → 打印学习")

    tab1, tab2, tab3 = st.tabs(["生成", "历史", "设置"])

    with tab1:
        render_generate_tab()
    with tab2:
        render_history_tab()
    with tab3:
        render_settings_tab()


if __name__ == "__main__":
    main()
