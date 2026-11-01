import logging
from dataclasses import dataclass

import tiktoken
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

try:
    ENCODING = tiktoken.encoding_for_model(settings.chat_model)
except KeyError:
    ENCODING = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text or ""))

def message_tokens(messages: list[dict[str, str]]) -> int:
    return sum(count_tokens(item["content"]) + 4 for item in messages) + 2

def _recent_start(messages: list[dict], turns: int) -> int:
    user_indices = [index for index, item in enumerate(messages) if item["role"] == "user"]
    if len(user_indices) <= turns:
        return 0
    return user_indices[-turns]

def _trim_tokens(text: str, maximum: int) -> str:
    ids = ENCODING.encode(text)
    return ENCODING.decode(ids[:maximum]) if len(ids) > maximum else text

async def _incremental_summary(previous: str, messages: list[dict]) -> str | None:
    if not messages or not settings.openai_api_key:
        return None
    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=12)
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in messages)
    try:
        response = await client.chat.completions.create(
            model=settings.chat_model,
            temperature=0,
            max_tokens=settings.context_summary_max_tokens,
            messages=[
                {"role": "system", "content": "你为家庭陪伴对话生成滚动摘要。只保留长辈明确说过且后续有用的事实：人物、时间、事件、明确偏好、未完成事项。不得推测，不添加建议，不把模型自己的话写成用户事实。把旧摘要与新增对话合并，输出简洁中文，长度不超过配置 token 上限。"},
                {"role": "user", "content": f"旧摘要（可能为空）：\n{previous or '（无）'}\n\n新增的较早对话：\n{transcript}"},
            ],
        )
        result = response.choices[0].message.content
        return result.strip() if result else None
    except Exception as exc:
        logger.warning("Rolling summary failed; falling back to oldest-message truncation: %s", type(exc).__name__)
        return None
    finally:
        await client.close()

@dataclass
class ContextBuild:
    summary: str
    raw_messages: list[dict[str, str]]
    input_tokens: int
    summary_tokens: int
    raw_tokens: int
    compressed: bool
    covered_through_message_id: str | None

async def build_context(system_prompt: str, history: list[dict], existing_summary: str = "", covered_id: str | None = None, current_text: str = "") -> ContextBuild:
    """Prepare the bounded prompt; summaries are incremental and raw history remains in business storage."""
    fresh = history
    if covered_id:
        covered_index = next((index for index, item in enumerate(history) if item["id"] == covered_id), None)
        if covered_index is not None:
            fresh = history[covered_index + 1:]
    summary = existing_summary
    summary_limit = max(1, settings.context_summary_max_tokens)
    budget = max(500, settings.context_token_budget)
    keep_turns = max(1, settings.context_keep_turns)
    start = _recent_start(fresh, keep_turns)
    compressed = False
    covered_through = covered_id

    def assemble(raw: list[dict], summary_text: str) -> tuple[int, int, int]:
        summary_tokens = count_tokens(summary_text)
        raw_tokens = message_tokens([{"role": item["role"], "content": item["content"]} for item in raw]) if raw else 0
        system_tokens = count_tokens(system_prompt) + 4
        if summary_text:
            system_tokens += summary_tokens + 8
        return system_tokens + raw_tokens + count_tokens(current_text) + (4 if current_text else 0), summary_tokens, raw_tokens

    total, _, _ = assemble(fresh, summary)
    if total > budget:
        compressed = True
        while start < len(fresh) - 1 and assemble(fresh[start:], summary)[0] > budget:
            start += 1
        old_messages = fresh[:start]
        raw = fresh[start:]
        next_summary = await _incremental_summary(summary, old_messages)
        if next_summary:
            summary = _trim_tokens(next_summary, summary_limit)
            if old_messages:
                covered_through = old_messages[-1]["id"]
        else:
            # No summary call available: retain only the most recent complete turns.
            logger.warning("Context compression used truncation fallback for conversation history")
        total, _, _ = assemble(raw, summary)
        while total > budget and raw:
            # No tools are enabled yet; discard the oldest raw messages only after the summary attempt.
            raw = raw[1:]
            total, _, _ = assemble(raw, summary)
        fresh = raw
    total, summary_tokens, raw_tokens = assemble(fresh, summary)
    if total > budget:
        logger.warning("Current message and system instructions exceed context budget")
    return ContextBuild(summary, [{"role": item["role"], "content": item["content"]} for item in fresh], total, summary_tokens, raw_tokens, compressed, covered_through)
