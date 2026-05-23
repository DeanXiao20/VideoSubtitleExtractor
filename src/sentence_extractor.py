import json
import logging
import re

from config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL, MAX_CLASSIC_SENTENCES

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """你是一位英语学习内容编辑。以下是视频的完整双语字幕。请完成两个任务：

1. 从中选出1-{max_count}句最经典、最值得学习的句子。经典标准：
   - 有哲理、发人深省
   - 关于逆境韧性和坚持
   - 对时代趋势的洞察
   - 关于人生价值和选择
   - 引发情感共鸣
   不要按生词难度选，而是按内容价值和启发性选。

   重要：确保每个经典句子语义完整。如果一个完整的意思跨越多条连续字幕，
   请将相关的所有字幕序号都包含进来，不要截断。
   例如如果第5条是"AI won't replace humans"而第6条是"But humans using AI will replace you"，
   这两句话强关联，你应该同时包含5和6作为一个经典句子。

2. 用2-3句中文概括视频的主要内容。

输出格式（严格JSON，不要添加markdown代码块标记）：
{{
  "summary": "视频简介...",
  "sentences": [[0], [5, 6], [33]]
}}

其中 sentences 中每个元素是一个序号列表：
- 单条完整句子：[5]
- 跨多条的关联句子：[5, 6] 表示第5-6条合并为一个经典句子

字幕内容：
{subtitles}"""


def _merge_segment_range(segments: list[dict], start: int, end: int) -> dict:
    if start == end:
        return dict(segments[start])

    orig_parts = []
    trans_parts = []
    annotations = []
    phrase_annotations = []
    offset = 0

    for i in range(start, end + 1):
        seg = segments[i]
        orig = seg.get("text_original", "").strip()
        trans = seg.get("text_translated", "").strip()

        if orig:
            orig_parts.append(orig)
        if trans:
            trans_parts.append(trans)

        for ann in seg.get("annotations", []):
            a = dict(ann)
            a["start_pos"] = a.get("start_pos", 0) + offset
            a["end_pos"] = a.get("end_pos", 0) + offset
            annotations.append(a)

        for pa in seg.get("phrase_annotations", []):
            p = dict(pa)
            p["start_pos"] = p.get("start_pos", 0) + offset
            p["end_pos"] = p.get("end_pos", 0) + offset
            phrase_annotations.append(p)

        offset += len(orig) + 1  # +1 for joining space

    return {
        "text_original": " ".join(orig_parts),
        "text_translated": " ".join(trans_parts),
        "start_time": segments[start].get("start_time"),
        "end_time": segments[end].get("end_time"),
        "annotations": annotations,
        "phrase_annotations": phrase_annotations,
    }


def _build_subtitle_text(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments):
        orig = seg.get("text_original", "").strip()
        trans = seg.get("text_translated", "").strip()
        if orig:
            lines.append(f"[{i}] {orig} | {trans}")
    return "\n".join(lines)


def _call_llm(
    segments: list[dict],
    api_key: str,
    api_base: str,
    model: str,
    max_count: int,
) -> dict | None:
    try:
        import http.client
        from urllib.parse import urlparse

        subtitle_text = _build_subtitle_text(segments)
        MAX_SUBTITLE_CHARS = 15000
        if len(subtitle_text) > MAX_SUBTITLE_CHARS:
            subtitle_text = subtitle_text[-MAX_SUBTITLE_CHARS:]
        prompt = _PROMPT_TEMPLATE.format(subtitles=subtitle_text, max_count=max_count)

        parsed = urlparse(api_base)
        host = parsed.hostname or "api.openai.com"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        base_path = parsed.path.rstrip("/")

        conn = http.client.HTTPSConnection(host, port, timeout=1800)
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 8192,
            },
            ensure_ascii=False,
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        conn.request(
            "POST", f"{base_path}/chat/completions", payload.encode("utf-8"), headers
        )
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()

        if resp.status != 200:
            raise RuntimeError(f"LLM API 返回错误 (HTTP {resp.status}): {body[:200]}")

        resp_json = json.loads(body)
        choices = resp_json.get("choices", [])
        if not choices:
            raise RuntimeError("LLM API 返回空结果")
        msg = choices[0].get("message", {})
        content = msg.get("content", "") or ""
        finish_reason = choices[0].get("finish_reason", "")

        # Some APIs (e.g. Baidu Qianfan) put the output in reasoning_content
        if not content:
            content = msg.get("reasoning_content", "") or ""

        if not content:
            if finish_reason == "length":
                raise RuntimeError(
                    "LLM 输出被截断（max_tokens 不足），请减少 MAX_CLASSIC_SENTENCES 或字幕长度"
                )
            raise RuntimeError("LLM 返回空内容")

        if finish_reason == "length":
            raise RuntimeError(
                "LLM 输出被截断（max_tokens 不足），请减少 MAX_CLASSIC_SENTENCES 或字幕长度"
            )

        content = re.sub(r"```json\s*", "", content)
        content = re.sub(r"```\s*", "", content)
        content = content.strip()

        # If content contains chain-of-thought before the JSON, extract just the JSON
        json_start = content.find("{")
        if json_start > 0:
            content = content[json_start:]

        # Try to fix truncated JSON by finding last complete array element
        if not content.rstrip().endswith("}"):
            logger.warning("LLM output appears truncated, attempting repair")
            # Find the last complete ] in sentences array
            bracket_pos = content.rfind("]")
            if bracket_pos > 0:
                # Close the sentences array and the outer object
                repaired = content[: bracket_pos + 1] + "}}"
                try:
                    result = json.loads(repaired)
                    if "sentences" in result and isinstance(result["sentences"], list):
                        logger.warning("Successfully repaired truncated JSON")
                        return result
                except json.JSONDecodeError:
                    pass
            raise RuntimeError(
                "LLM 输出被截断，无法解析。请减少 MAX_CLASSIC_SENTENCES 或尝试更长的模型"
            )

        result = json.loads(content)
        if "sentences" not in result or not isinstance(result["sentences"], list):
            raise RuntimeError("LLM 返回格式缺少 sentences 字段")

        return result

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise RuntimeError(f"LLM 返回结果解析失败: {e}") from e
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"LLM 调用失败: {e}") from e


def _get_llm_config(settings: dict | None = None) -> tuple[str, str, str]:
    settings = settings or {}
    api_key = settings.get("llm_api_key", "") or LLM_API_KEY
    api_base = settings.get("llm_api_base", "") or LLM_API_BASE
    model = settings.get("llm_model", "") or LLM_MODEL
    return api_key, api_base, model


def extract_extras(
    segments: list[dict],
    video_info: dict,
    settings: dict | None = None,
    max_count: int | None = None,
    progress_callback=None,
) -> tuple[str, list[dict], bool]:
    """Extract video summary and classic sentences via LLM.

    Returns (summary, classic_sentences, used_llm).
    Raises RuntimeError if LLM is not configured or call fails.
    """
    if max_count is None:
        max_count = MAX_CLASSIC_SENTENCES

    if progress_callback:
        progress_callback("extract", 0.1, "正在准备字幕数据...")

    api_key, api_base, model = _get_llm_config(settings)

    if not api_key:
        raise RuntimeError("未配置 LLM API Key，请在 .env 文件中设置 LLM_API_KEY")

    if not segments:
        raise RuntimeError("没有可用的字幕数据")

    if progress_callback:
        progress_callback("extract", 0.3, "正在调用 AI 分析经典句子和简介...")

    llm_result = _call_llm(segments, api_key, api_base, model, max_count)

    if progress_callback:
        progress_callback("extract", 0.7, "AI 分析完成，正在整理结果...")

    summary = llm_result.get("summary", "")
    if not summary:
        description = video_info.get("description", "")
        if description and len(description.strip()) > 10:
            summary = description.strip()

    indices = llm_result.get("sentences", [])
    classic = []
    for item in indices:
        if isinstance(item, list) and len(item) > 0:
            valid = [i for i in item if isinstance(i, int) and 0 <= i < len(segments)]
            if valid:
                merged = _merge_segment_range(segments, min(valid), max(valid))
                classic.append(merged)
        elif isinstance(item, int) and 0 <= item < len(segments):
            classic.append(dict(segments[item]))

    if len(classic) > max_count:
        classic = classic[:max_count]

    classic.sort(key=lambda s: s.get("start_time", 0) or 0)

    return summary, classic, True


def extract_classic_sentences(
    segments: list[dict],
    settings: dict | None = None,
    max_count: int = 10,
) -> list[dict]:
    _, classic, _ = extract_extras(segments, {}, settings, max_count)
    return classic


def generate_video_summary(
    video_info: dict,
    segments: list[dict],
    settings: dict | None = None,
) -> str:
    summary, _, _ = extract_extras(segments, video_info, settings)
    return summary
