import json
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SETTINGS_PATH, DATA_DIR, OUTPUT_DIR, CEFR_DIFFICULTY_THRESHOLD, WHISPER_MODEL_SIZE, APP_PORT, MAX_CLASSIC_SENTENCES


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


def _get_share_url(filename: str) -> str:
    from src.share_server import _get_local_ip
    frp_url = st.session_state.settings.get("frp_public_url", "") if "settings" in st.session_state else ""
    if frp_url:
        base = frp_url.rstrip("/")
        return f"{base}/output/{filename}"
    ip = _get_local_ip()
    return f"http://{ip}:{APP_PORT}/output/{filename}"


def regenerate_html_with_extras(video: dict, settings: dict) -> tuple[int, bool]:
    from src.database import Database
    from src.sentence_extractor import extract_extras
    from src.html_generator import generate_bilingual_html

    db = Database()
    vid = video["id"]
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
                segments, video, settings,
                max_count=MAX_CLASSIC_SENTENCES,
                progress_callback=progress_callback,
            )
        except RuntimeError as e:
            status.update(label="提取失败", state="error", expanded=False)
            db.close()
            st.error(str(e))
            return 0, False

        status_steps["text"].markdown("**正在生成 HTML...**")
        status_steps["bar"].progress(0.9)

        db.save_video_extras(vid, summary, classic)
        output_path = OUTPUT_DIR / f"{video.get('video_id', 'output')}.html"
        generate_bilingual_html(video, segments, output_path, summary, classic)

        status.update(label="提取完成（AI 分析）", state="complete", expanded=False)

    db.close()
    return len(classic), used_llm


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

    col1, col2, col3 = st.columns(3)
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
        current_model = st.session_state.settings.get("whisper_model", WHISPER_MODEL_SIZE)
        model_choice = st.selectbox(
            "Whisper模型",
            options=list(model_display.keys()),
            format_func=lambda x: model_display.get(x, x),
            index=list(model_display.keys()).index(current_model) if current_model in model_display else 2,
        )

    generate_clicked = st.button("一键生成双语字幕", type="primary", use_container_width=True)

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
        st.caption(f"来源: {info.get('url', '')}")
        video_url = info.get("url", "")
        if video_url and video_url.startswith("http"):
            st.link_button("▶ 打开原视频", video_url)

    # Action buttons
    btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)
    with btn_col1:
        if st.button("🖨️ 打印", use_container_width=True):
            st.components.v1.html(
                '<script>window.print();</script>',
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
        phrase_count = sum(1 for seg in result.segments if seg.get("phrase_annotations"))
        info_text = f"生词: {difficult_count}段"
        if phrase_count:
            info_text += f" | 短语: {phrase_count}段"
        st.info(info_text)
    with btn_col4:
        if st.button("提取简介和经典句子", use_container_width=True, key="gen_extract"):
            count, used_llm = regenerate_html_with_extras(info, dict(st.session_state.settings))
            if count > 0:
                st.success(f"已提取简介和 {count} 句经典句子")
                st.rerun()
    with btn_col5:
        if st.button("📱 微信分享", use_container_width=True):
            from src.share_server import generate_qr_code
            filename = f"{info.get('video_id', 'output')}.html"
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
                st.caption("同一WiFi下直接扫码。外网访问需配置frp，见设置页。如无法访问，请以管理员身份运行cmd开放端口: netsh advfirewall firewall add rule name=Share dir=in action=allow protocol=tcp localport=" + str(APP_PORT))

    # HTML preview
    st.markdown("### 预览")
    with st.container(height=600):
        st.components.v1.html(result.html, height=600, scrolling=True)


@st.dialog("双语字幕预览", width="large")
def show_preview_dialog(video: dict, segments: list[dict]):
    from src.annotator import _load_phrase_dict, _find_phrases
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

    html_content = generate_bilingual_html(video, segments, video_summary="", classic_sentences=None)

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
            st.components.v1.html('<script>window.print();</script>', height=0)
    with col3:
        if st.button("📱 微信分享"):
            from src.share_server import generate_qr_code
            filename = f"{video.get('video_id', 'output')}.html"
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
                st.caption("手机需与电脑在同一WiFi网络下。如无法访问，请以管理员身份运行cmd: netsh advfirewall firewall add rule name=Share dir=in action=allow protocol=tcp localport=" + str(APP_PORT))

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


def render_history_tab() -> None:
    from src.database import Database
    db = Database()

    search = st.text_input("搜索视频标题...", placeholder="输入关键词")

    videos = db.get_recent_videos(limit=50)
    if search.strip():
        videos = [v for v in videos if search.lower() in (v.get("title") or "").lower()]

    if not videos:
        st.info("暂无处理记录")
        db.close()
        return

    for video in videos:
        vid = video["id"]
        with st.container(border=True):
            title_col, action_col = st.columns([3, 1])
            with title_col:
                st.markdown(f"**{video.get('title', '未知标题')}**")
                duration_s = video.get("duration_seconds", 0)
                m, s = divmod(int(duration_s or 0), 60)
                h, m = divmod(m, 60)
                dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                st.caption(
                    f"作者: {video.get('author', '未知')} | "
                    f"时长: {dur_str} | "
                    f"平台: {video.get('platform', '未知')} | "
                    f"时间: {video.get('updated_at', '')[:10]}"
                )
                if video.get("url"):
                    video_url = video["url"]
                    if video_url.startswith("http"):
                        st.link_button("▶ 打开原视频", video_url, key=f"open_{vid}")
                    else:
                        st.caption(f"链接: {video_url}")
            with action_col:
                video_id = video.get("video_id", "")
                html_path = OUTPUT_DIR / f"{video_id}.html"
                if html_path.exists():
                    st.link_button("查看", f"/output/{video_id}.html", use_container_width=True)
                else:
                    st.caption("无HTML文件")
                if st.button("提取简介和经典句子", key=f"extract_{vid}", use_container_width=True):
                    count, used_llm = regenerate_html_with_extras(video, dict(st.session_state.settings))
                    if count > 0:
                        st.success(f"已提取简介和 {count} 句经典句子")
                        st.rerun()
                if st.button("删除", key=f"del_{vid}", use_container_width=True):
                    confirm_delete_dialog(db, vid, video.get("title", "未知标题"))

            with st.expander("编辑信息"):
                new_title = st.text_input(
                    "标题", value=video.get("title", ""), key=f"edit_title_{vid}"
                )
                new_url = st.text_input(
                    "视频链接", value=video.get("url", ""), key=f"edit_url_{vid}"
                )
                if st.button("保存修改", key=f"save_{vid}"):
                    db.update_video(vid, title=new_title, url=new_url)
                    st.success("已保存")
                    st.rerun()

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
        index=list(model_display.keys()).index(settings.get("whisper_model", WHISPER_MODEL_SIZE))
        if settings.get("whisper_model", WHISPER_MODEL_SIZE) in model_display else 2,
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
        index=list(threshold_display.keys()).index(settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD))
        if settings.get("difficulty_threshold", CEFR_DIFFICULTY_THRESHOLD) in threshold_display else 0,
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
    st.caption("编辑项目根目录 `.env` 文件修改配置（LLM_API_KEY / LLM_API_BASE / LLM_MODEL）")

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
        st.caption(f"将此配置保存为 frpc.toml，运行 `frpc -c frpc.toml` 即可。本地服务端口为 {APP_PORT}。")


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
