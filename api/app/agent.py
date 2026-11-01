import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from jsonschema import Draft202012Validator
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from openai import AsyncOpenAI
from sqlalchemy import create_engine, text

from app.config import settings
from app.tools import ToolContext, execute_tool, openai_tool_schemas
from app.context_manager import count_tokens
from app.tools import TOOL_REGISTRY
from app.confirmations import create_reminder_candidate, create_reminder_change_candidate, confirmation_node
from app.memory_service import is_memory_opt_out
from app.observability import trace_event

SYSTEM_PROMPT = "你是 MiniClaw，一款面向长辈的陪伴程序。请用温和、简洁、自然的中文交流。你不是用户真实的家人；不要编造家庭事实、通话或承诺。创建提醒前必须具备事项、明确的具体日期时间、时区和重复规则；任何字段缺失或含糊都先追问，绝不猜测。修改、完成、暂停、继续或取消提醒前，必须先读取目标提醒并通过后端确认节点提出，不能把口头‘嗯/好/啊’当作确认。当前不提供医疗诊断或治疗建议。"
logger = logging.getLogger(__name__)
MAX_TOOL_STEPS = 3
MAX_TOOL_CALLS = 4
MAX_CONSECUTIVE_TOOL_FAILURES = 3

class AgentState(MessagesState, total=False):
    pending_confirmation: dict | None

def _turn_update(state: MessagesState, answer: str):
    latest_user = next((message for message in reversed(state["messages"]) if isinstance(message, HumanMessage)), None)
    removals = [RemoveMessage(id=message.id) for message in state["messages"] if message.id != (latest_user.id if latest_user else None)]
    return {"messages": [*removals, AIMessage(content=answer)]}

async def _chat_node(state: AgentState, config):
    writer = get_stream_writer()
    latest_user = next((str(message.content) for message in reversed(state["messages"]) if isinstance(message, HumanMessage)), "")
    if is_memory_opt_out(latest_user):
        answer = "好，不记。"
        writer({"token": answer})
        return _turn_update(state, answer)
    if re.search(r"你是不是真|你是真人吗|你是真实的|你是我(女儿|孙女)|你真的是(女儿|孙女|真人)|你是不是(我)?(女儿|孙女|真人)|你是不是我", latest_user):
        answer = "我不是真人，也不是现实中的家人；我是 MiniClaw 陪伴程序，会用您选择的角色方式陪您聊天。"
        writer({"token": answer})
        return _turn_update(state, answer)
    if not settings.openai_api_key:
        answer = "你好呀，很高兴和你聊聊天。现在还没有配置 AI 服务密钥，请家人协助完成设置后再试。"
        writer({"token": answer})
        return _turn_update(state, answer)

    context_config = config.get("configurable", {})
    prompt_messages = [
        {"role": "system", "content": context_config.get("system_prompt", SYSTEM_PROMPT)},
        *([{"role": "system", "content": "此前对话的滚动摘要（仅作参考；若与当前明确表述冲突，以当前明确表述为准）：" + context_config["summary"]}] if context_config.get("summary") else []),
        *context_config.get("context_messages", []),
        {"role": "user", "content": latest_user},
    ]
    configurable = config.get("configurable", {})
    allowed_tools = frozenset(configurable.get("allowed_tools", []))
    tool_context = ToolContext(family_id=configurable.get("family_id", settings.family_id), member_id=configurable.get("member_id", settings.member_id), default_timezone=configurable.get("family_timezone", settings.family_timezone), allowed_tools=allowed_tools, request_id=configurable.get("request_id"))
    consecutive_failures = 0
    tool_calls_count = 0
    stop_tools = False
    tool_audit: list[dict] = []
    parts: list[str] = []
    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=15)
    try:
        for step in range(MAX_TOOL_STEPS + 1):
            tools_enabled = bool(allowed_tools) and step < MAX_TOOL_STEPS and not stop_tools and tool_calls_count < MAX_TOOL_CALLS and consecutive_failures < MAX_CONSECUTIVE_TOOL_FAILURES
            llm_started = time.perf_counter()
            streamed_text: list[str] = []
            pending_calls: dict[int, dict] = {}
            finish_reason = None
            try:
                response = await client.chat.completions.create(
                    model=settings.chat_model,
                    messages=prompt_messages,
                    tools=openai_tool_schemas(allowed_tools) if tools_enabled else None,
                    tool_choice="auto" if tools_enabled else None,
                    stream=True,
                )
                async for chunk in response:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    if delta.content:
                        streamed_text.append(delta.content)
                        parts.append(delta.content)
                        writer({"token": delta.content})
                    for call_delta in delta.tool_calls or []:
                        call = pending_calls.setdefault(call_delta.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if call_delta.id: call["id"] = call_delta.id
                        if call_delta.function:
                            if call_delta.function.name: call["function"]["name"] += call_delta.function.name
                            if call_delta.function.arguments: call["function"]["arguments"] += call_delta.function.arguments
            except BaseException as exc:
                trace_event("llm_step", int((time.perf_counter() - llm_started) * 1000), "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", {"step": step + 1, "model": settings.chat_model})
                raise
            trace_event("llm_step", int((time.perf_counter() - llm_started) * 1000), "ok", {"step": step + 1, "model": settings.chat_model, "tool_calls": len(pending_calls)})
            if not tools_enabled or finish_reason != "tool_calls" or not pending_calls:
                break

            calls = [pending_calls[index] for index in sorted(pending_calls)]
            prompt_messages.append({"role": "assistant", "content": "".join(streamed_text) or None, "tool_calls": calls})
            available_calls = max(0, MAX_TOOL_CALLS - tool_calls_count)
            executable_calls = calls[:available_calls]
            skipped_calls = calls[available_calls:]
            if skipped_calls:
                stop_tools = True
            parsed: list[tuple[dict, str, dict | None]] = []
            for call in executable_calls:
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                    if not isinstance(arguments, dict): arguments = None
                except (json.JSONDecodeError, TypeError):
                    arguments = None
                parsed.append((call, call["function"]["name"], arguments))
            proposal_items = [item for item in parsed if TOOL_REGISTRY.get(item[1]) and TOOL_REGISTRY[item[1]].requires_confirmation]
            regular_items = [item for item in parsed if item not in proposal_items]
            results = await asyncio.gather(*(execute_tool(name, arguments, tool_context) for _, name, arguments in regular_items))
            result_items = list(zip(regular_items, results))
            for call in skipped_calls:
                result_items.append(((call, call["function"]["name"], None), {"ok": False, "error": "本轮查询次数已到上限。", "queried_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "duration_ms": 0}))
            for (call, name, arguments), result in result_items:
                tool_calls_count += 1
                if not result.get("ok"):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                audit = {"name": name, "arguments": arguments or {}, **result}
                trace_event("tool_call", int(result.get("duration_ms", 0)), "ok" if result.get("ok") else "failed", {"tool_name": name})
                tool_audit.append(audit)
                writer({"tool_call": audit})
                candidate_result = json.dumps(result, ensure_ascii=False)
                prompt_size = sum(count_tokens(str(message.get("content") or "")) + 4 for message in prompt_messages)
                if prompt_size + count_tokens(candidate_result) > settings.context_token_budget:
                    result["result"] = "查询已执行；因上下文空间有限，较长结果已省略。"
                    result["truncated"] = True
                    candidate_result = json.dumps(result, ensure_ascii=False)
                if prompt_size + count_tokens(candidate_result) > settings.context_token_budget:
                    result["result"] = "结果已省略，请稍后再查。"
                    result["truncated"] = True
                prompt_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
            if proposal_items:
                call, name, arguments = proposal_items[0]
                if len(proposal_items) > 1:
                    candidate = {"ok": False, "needs_clarification": True, "message": "每次只能确认一条提醒。"}
                elif name not in allowed_tools:
                    candidate = {"ok": False, "needs_clarification": True, "message": "当前陪伴角色没有处理提醒的权限，请让家属在设置中开启。"}
                else:
                    spec = TOOL_REGISTRY[name]
                    schema_errors = list(Draft202012Validator(spec.parameters).iter_errors(arguments or {}))
                    if schema_errors:
                        candidate = {"ok": False, "needs_clarification": True, "message": "提醒信息缺少必要字段或格式不正确，请先向长辈补问。"}
                    elif name == "propose_reminder_change":
                        candidate = create_reminder_change_candidate(arguments or {}, tool_context, context_config["thread_id"], call["id"], latest_user)
                    else:
                        candidate = create_reminder_candidate(arguments or {}, tool_context, context_config["thread_id"], call["id"], latest_user)
                if candidate.get("confirmation_id"):
                    writer({"confirmation_candidate": candidate})
                    state_calls = [{"name": item[1], "args": item[2] or {}, "id": item[0]["id"], "type": "tool_call"} for item in parsed]
                    state_messages = [AIMessage(content="".join(streamed_text) or None, tool_calls=state_calls)]
                    state_messages.extend(ToolMessage(content=json.dumps(item[1], ensure_ascii=False), tool_call_id=item[0]["id"]) for item in result_items)
                    return {"messages": state_messages, "pending_confirmation": candidate}
                prompt_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"ok": False, **candidate}, ensure_ascii=False)})
                writer({"tool_call": {"name": name, "arguments": arguments or {}, **candidate, "queried_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "duration_ms": 0}})
            if stop_tools or consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                stop_tools = True
                notice = "本轮查询次数已到上限。" if tool_calls_count >= MAX_TOOL_CALLS else "刚才连续几次没查到，先根据已有信息回答，并说明暂时没查到。"
                prompt_messages.append({"role": "system", "content": notice})
        if not parts:
            fallback = "我暂时没能回答出来，咱们稍后再试一次好吗？"
            parts.append(fallback)
            writer({"token": fallback})
    finally:
        await client.close()
    return {"messages": _turn_update(state, "".join(parts))["messages"]}

def build_graph(checkpointer):
    builder = StateGraph(AgentState)
    builder.add_node("chat", _chat_node)
    async def confirm_node(state: AgentState):
        return await confirmation_node(state, get_stream_writer())
    builder.add_node("confirm", confirm_node)
    builder.add_edge(START, "chat")
    builder.add_conditional_edges("chat", lambda state: "confirm" if state.get("pending_confirmation") else "end", {"confirm": "confirm", "end": END})
    builder.add_edge("confirm", END)
    return builder.compile(checkpointer=checkpointer)

@asynccontextmanager
async def checkpointed_graph(database_url: str, schema: str):
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    engine.dispose()
    checkpoint_url = database_url + f"?options=-csearch_path%3D{schema}"
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as checkpointer:
        await checkpointer.setup()
        yield build_graph(checkpointer)
