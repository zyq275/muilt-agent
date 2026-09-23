"""
主智能体组装 + 反思循环执行 — TDD §4.2 / §5.2

组装：
    create_deep_agent(model, tools=[三个文件工具], system_prompt, subagents=[三个子智能体])

反思循环（严格照抄 TDD §5.2 伪代码）：
    MAX_ROUNDS = 3；先硬校验（H1~H4），再软校验（提示词驱动）；
    不通过则生成纠错指令注入下一轮；达上限返回最优结果 + 未达标原因。
"""

import asyncio
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, NamedTuple

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool

from agent.llm import get_llm
from agent.load_prompt import (
    apply_security_boundary,
    main_agent_config,
    sub_agents_config,
)
from agent.sub_agents.web_search_agent import get_web_search_subagent
from agent.sub_agents.db_query_agent import get_db_query_subagent
from agent.sub_agents.knowledge_agent import get_knowledge_subagent
from api.context import set_session, get_session_dir, reset_session
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content
from tools.reflect_check import hard_check
from utils.logger_utils import get_logger
from utils.path_utils import UPLOAD_DIR

logger = get_logger(__name__)

# ── 循环参数（TDD §5.2） ───────────────────────────────
MAX_ROUNDS = 3

# 子智能体 name → 数据源标识 的反向映射（供 H4 判定）
_NAME_TO_SOURCE: dict[str, str] = {
    cfg["name"]: key for key, cfg in sub_agents_config.items()
}

# 软校验提示词（提示词驱动，语义级）
SOFT_REVIEW_PROMPT = """你是一名严格的技术审稿人。请审阅以下报告，从三个角度判断是否达标：

1. 是否完整回答了用户的问题？
2. 数据是否充分支撑结论？
3. 逻辑是否一致、有无自相矛盾？

报告全文如下：
---
{report}
---

请只输出以下两种格式之一，不要输出其他内容：
- 若达标，只输出：通过
- 若不达标，输出：不通过 + 具体问题描述（一句话）

注意：若正文中出现"（此处省略 N 字符…）"标记，那是系统为控制长度做的省略，
不属于报告内容缺失，不要据此判定"正文被截断"。"""

# 送审正文长度上限（字符）。
# 超过才做首尾保留，且必须显式标注省略——绝不能静默截断：
# 审稿人看到残缺正文会如实报告"报告被截断"，而实际上是我方截的，
# 智能体于是为一个并不存在的问题反复返工，反思循环永远不收敛。
SOFT_REVIEW_MAX_CHARS = 30000

# 数据源声明指令（追加到主智能体系统提示词，供 H4 判定）
# 注：prompt/prompts.yaml 保持 TDD §6 原文不动，此段在组装时追加。
SOURCE_DECLARATION_INSTRUCTION = """

【数据源声明规则】（规划阶段必须执行）
在生成 todo-list 之后，必须紧接着单独输出一行数据源声明，格式严格如下：
【数据源声明】web_search, db_query, knowledge
- 只填本次任务确实需要的数据源，不需要的不要写；
- 可用标识仅三个：web_search（网络公开信息）、db_query（企业结构化业务数据）、knowledge（企业内部知识文档）；
- 声明后，后续必须实际调用所声明的每一个子智能体。"""

# 数据源声明解析正则
_SOURCE_DECLARATION_RE = re.compile(r"【数据源声明】\s*([^\n]*)")

# 数据源声明的合法标识
_VALID_SOURCES = {"web_search", "db_query", "knowledge"}


def _to_tool(fn: Any) -> BaseTool:
    """
    把普通函数包装成 langchain @tool（TDD §2 要求 @tool 装饰器）。

    已是 BaseTool 的直接返回，避免重复包装。
    """
    if isinstance(fn, BaseTool):
        return fn
    return tool(fn)


# ── 主智能体组装 ──────────────────────────────────────

def build_main_agent():
    """
    用 create_deep_agent 组装主智能体。

    组成：
    - 模型：agent.llm.get_llm()
    - 系统提示词：prompt/prompts.yaml 的 main_agent.system_prompt + 数据源声明规则
    - 工具：generate_markdown / convert_md_to_pdf / read_file_content（三个文件工具，@tool 包装）
    - 子智能体：web_search / db_query / knowledge

    Returns:
        编译好的 DeepAgent 图（CompiledStateGraph）。
    """
    from deepagents import create_deep_agent

    subagents = [
        get_web_search_subagent(),
        get_db_query_subagent(),
        get_knowledge_subagent(),
    ]

    system_prompt = apply_security_boundary(
        main_agent_config["system_prompt"] + SOURCE_DECLARATION_INSTRUCTION
    )

    agent = create_deep_agent(
        model=get_llm(),
        tools=[
            _to_tool(generate_markdown),
            _to_tool(convert_md_to_pdf),
            _to_tool(read_file_content),
        ],
        system_prompt=system_prompt,
        subagents=subagents,
    )

    logger.info(f"主智能体已组装：3 个子智能体 + 3 个文件工具，MAX_ROUNDS={MAX_ROUNDS}")
    return agent


# ── 会话工作区准备（伪代码第 2 步） ─────────────────────

def _prepare_workspace(thread_id: str) -> str:
    """
    创建工作目录 + 复制上传文件 + 生成工作环境指令。

    前置：调用方已 set_session(thread_id)。

    Args:
        thread_id: 会话标识。

    Returns:
        工作环境指令文本（将拼接进本轮 query）。
    """
    session_dir = get_session_dir()
    upload_dir = UPLOAD_DIR / f"session_{thread_id}"

    copied: list[str] = []
    if upload_dir.exists():
        for src in upload_dir.iterdir():
            if src.is_file():
                try:
                    shutil.copy2(src, session_dir / src.name)
                    copied.append(src.name)
                except Exception as e:
                    logger.error(f"复制上传文件失败 {src.name}：{e}")

    instruction = (
        f"\n\n【工作环境】\n"
        f"你的工作目录为：{session_dir}\n"
        f"所有文件的创建、读取、保存只能在该目录内进行，使用相对路径。\n"
    )
    if copied:
        instruction += f"用户已上传以下文件到工作目录：{', '.join(copied)}\n"

    logger.info(f"工作区已准备：{session_dir}，复制上传文件 {len(copied)} 个")
    return instruction


def _collect_produced_files() -> list[str]:
    """列出当前会话工作目录中的所有文件名（供 H3 校验）。"""
    session_dir = get_session_dir()
    if session_dir is None or not session_dir.exists():
        return []
    return [p.name for p in session_dir.iterdir() if p.is_file()]


# ── 单轮执行（伪代码第 3.1 步） ─────────────────────────

def _extract_tool_calls(msg: Any) -> list[dict]:
    """从消息中提取 tool_calls，兼容消息对象与 dict 两种形态。"""
    if isinstance(msg, dict):
        return msg.get("tool_calls") or []
    return getattr(msg, "tool_calls", None) or []


def _msg_type(msg: Any) -> str:
    """获取消息类型（ai / tool / human），兼容消息对象与 dict 两种形态。"""
    if isinstance(msg, dict):
        return msg.get("type", "")
    return getattr(msg, "type", "")


def _extract_content(msg: Any) -> str:
    """从消息中提取文本内容，兼容消息对象与 dict 两种形态。"""
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
    if isinstance(content, list):
        # 多模态内容块 → 拼接文本块
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content or ""


class RoundResult(NamedTuple):
    """单轮执行结果。"""

    reply_text: str          # 主智能体的对话回复（可能只是"已成功创建 xx"）
    called_sources: set[str]  # 本轮实际调用的数据源集合
    ai_texts: list[str]      # 本轮全部 AI 文本（供解析数据源声明）


async def main_agent_run(
    agent,
    query: str,
    round_num: int,
    on_event: Callable[[str, str, dict], None] | None = None,
) -> RoundResult:
    """
    执行一轮主智能体（规划 + 执行，提示词驱动）。

    Args:
        agent: build_main_agent() 返回的图。
        query: 本轮任务描述（含纠错指令）。
        round_num: 轮次编号（从 1 开始）。
        on_event: 可选事件回调 on_event(event_type, message, data)，
                  供接入层推送 WebSocket 事件（TDD §8.2）。

    Returns:
        RoundResult(reply_text, called_sources, ai_texts)
    """
    final_text = ""
    called_sources: set[str] = set()
    ai_texts: list[str] = []

    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=query)]},
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            continue

        for _node, update in chunk.items():
            if not isinstance(update, dict):
                continue

            for msg in update.get("messages", []) or []:
                tool_calls = _extract_tool_calls(msg)

                for call in tool_calls:
                    name = call.get("name", "")
                    args = call.get("args", {}) or {}

                    if name == "task":
                        # 子智能体调度（TDD §8.2：工具名为 task 判定为 assistant_call）
                        subagent_type = args.get("subagent_type", "")
                        source_key = _NAME_TO_SOURCE.get(subagent_type)
                        if source_key:
                            called_sources.add(source_key)

                        if on_event:
                            on_event(
                                "assistant_call",
                                f"调用子智能体：{subagent_type}",
                                {
                                    "assistant_name": subagent_type,
                                    "args": args.get("description", ""),
                                },
                            )
                        logger.info(f"[第 {round_num} 轮] 调度子智能体：{subagent_type}")
                    else:
                        # 普通工具调用
                        if on_event:
                            on_event(
                                "tool_start",
                                f"调用工具：{name}",
                                {"tool_name": name, "args": args},
                            )
                        logger.info(f"[第 {round_num} 轮] 调用工具：{name}")

                # 收集 AI 文本
                # 注意：数据源声明常与子智能体调度（task 工具调用）同处一条 AI 消息，
                #      因此不能按 "无 tool_calls" 过滤，必须留档全部 AI 文本。
                if _msg_type(msg) == "ai":
                    content = _extract_content(msg)
                    if content and content.strip():
                        ai_texts.append(content)
                        # 最终回答 = 最后一条无 tool_calls 的 AI 消息
                        if not tool_calls:
                            final_text = content

    logger.info(
        f"[第 {round_num} 轮] 执行完毕：回复 {len(final_text)} 字符，"
        f"调用数据源 {sorted(called_sources) or '无'}"
    )
    return RoundResult(
        reply_text=final_text,
        called_sources=called_sources,
        ai_texts=ai_texts,
    )


def _extract_declared_sources(ai_texts: list[str]) -> set[str] | None:
    """
    从本轮 AI 文本中解析主智能体在规划阶段声明的数据源（H4 判定依据）。

    主智能体应输出形如：`【数据源声明】web_search, db_query, knowledge`

    Args:
        ai_texts: 本轮全部 AI 文本。

    Returns:
        解析成功返回声明集合（可能为空集，表示"本次无需任何数据源"）；
        未找到声明行返回 None。
    """
    for text in ai_texts:
        match = _SOURCE_DECLARATION_RE.search(text)
        if not match:
            continue

        raw = match.group(1)
        declared = {
            token.strip()
            for token in re.split(r"[,，、\s]+", raw)
            if token.strip()
        }
        valid = declared & _VALID_SOURCES
        if invalid := declared - _VALID_SOURCES:
            logger.warning(f"数据源声明含非法标识，已忽略：{sorted(invalid)}")
        logger.info(f"解析到数据源声明：{sorted(valid) or '空'}")
        return valid

    return None


def _collect_report_text(fallback: str) -> str:
    """
    收集用于 H1/H2 校验的报告正文（问题一 · 方案 A）。

    优先读取会话工作目录中最近产出的 .md 文件内容；
    若不存在或读取失败，回退为主智能体的对话回复文本。

    原因：系统提示词要求主智能体"只通知已成功创建、不发送文件路径"，
    对话回复往往只有几个字，直接用它做篇幅校验会永远不通过。

    Args:
        fallback: 对话回复文本（兜底）。

    Returns:
        用于校验的报告正文。
    """
    session_dir = get_session_dir()
    if session_dir is not None and session_dir.exists():
        md_files = [p for p in session_dir.glob("*.md") if p.is_file()]
        if md_files:
            # 取最近修改的（报告最后生成），同时间戳时取文件更大的
            md_files.sort(
                key=lambda p: (p.stat().st_mtime, p.stat().st_size), reverse=True
            )
            chosen = md_files[0]
            try:
                content = chosen.read_text(encoding="utf-8")
                if content.strip():
                    logger.info(
                        f"校验正文取自文件：{chosen.name}（{len(content)} 字符）"
                    )
                    return content
            except Exception as e:
                logger.error(f"读取报告文件失败 {chosen.name}：{e}")

    logger.info("工作目录无 .md 报告文件，校验正文回退为对话回复")
    return fallback


# ── 软校验（伪代码第 3.3 步） ───────────────────────────

def _prepare_review_text(report: str) -> str:
    """
    准备送审正文。

    未超限时原样返回全文；超限时保留首尾并显式标注省略量。
    显式标注是关键：审稿人会把静默截断的输入误判为"报告本身被截断"。

    Args:
        report: 报告全文。

    Returns:
        送审文本。
    """
    if len(report) <= SOFT_REVIEW_MAX_CHARS:
        return report

    half = SOFT_REVIEW_MAX_CHARS // 2
    head, tail = report[:half], report[-half:]
    omitted = len(report) - len(head) - len(tail)
    logger.warning(
        f"报告长度 {len(report)} 超过送审上限 {SOFT_REVIEW_MAX_CHARS}，"
        f"保留首尾、省略中间 {omitted} 字符"
    )
    return (
        f"{head}\n\n"
        f"……（此处省略 {omitted} 字符，由系统为控制长度而省略，非报告内容缺失）……\n\n"
        f"{tail}"
    )


async def soft_review(report: str) -> str | None:
    """
    软校验：向主智能体追加一轮"反思"提示词，以审稿人视角审阅报告。

    Args:
        report: 报告全文。

    Returns:
        通过返回 None；不通过返回问题描述。
    """
    if not report.strip():
        return "报告内容为空"

    try:
        llm = get_llm()
        review_text = _prepare_review_text(report)
        logger.info(f"送审正文 {len(review_text)} 字符（原文 {len(report)} 字符）")
        resp = await llm.ainvoke(SOFT_REVIEW_PROMPT.format(report=review_text))
        text = (resp.content or "").strip()

        # 注意：必须先判断"不通过"，因为它是"通过"的超串
        if "不通过" in text:
            logger.info(f"软校验未通过：{text}")
            return text

        if "通过" in text:
            logger.info("软校验通过")
            return None

        # 模型未按格式输出 → 保守视为通过，避免死循环
        logger.warning(f"软校验输出格式异常，按通过处理：{text[:100]}")
        return None

    except Exception as e:
        logger.error(f"软校验异常，按通过处理：{e}")
        return None


# ── 纠错指令生成（伪代码第 3.5 步） ─────────────────────

def build_correction_prompt(
    original_query: str,
    hard_issues: list[str],
    soft_issue: str | None,
) -> str:
    """
    将未通过项拼成明确的纠错指令，注入下一轮 query。

    Args:
        original_query: 原始任务描述。
        hard_issues: 硬校验未通过项列表。
        soft_issue: 软校验问题描述。

    Returns:
        纠错后的新一轮 query。
    """
    lines = [
        original_query,
        "",
        "【上一轮输出未达标，请针对以下问题修正后重新执行】",
    ]
    for issue in hard_issues:
        lines.append(f"- {issue}")
    if soft_issue:
        lines.append(f"- 语义审查问题：{soft_issue}")

    lines.append("")
    lines.append("请务必先补充检索、再重新生成完整报告，不要保留任何占位内容。")

    return "\n".join(lines)


# ── 辅助：从 query 推断校验上下文 ───────────────────────

def infer_require_pdf(query: str) -> bool:
    """从用户 query 推断是否要求产出 PDF。"""
    return "pdf" in query.lower()


def infer_required_sources(query: str) -> set[str]:
    """
    从用户 query 推断应当调用的数据源集合（H4 判定依据）。

    注：TDD §5.2 指出"应调用某数据源由主智能体在规划阶段声明"。
    本实现采用关键词启发式作为规划声明的轻量代理；
    接入层也可通过 run_deep_agent(required_sources=...) 显式覆盖。

    Args:
        query: 用户任务描述。

    Returns:
        数据源标识集合，取值 {"web_search", "db_query", "knowledge"}。
    """
    sources: set[str] = set()

    if any(k in query for k in ("内部", "知识库", "私有", "制度", "规范", "手册", "资料", "文档")):
        sources.add("knowledge")

    if any(k in query for k in ("数据库", "库存", "销售", "销量", "表", "MySQL", "mysql", "药品信息", "批次")):
        sources.add("db_query")

    if any(k in query for k in ("网络", "公开", "最新", "新闻", "政策", "法规", "市场", "行业", "网上")):
        sources.add("web_search")

    return sources


# ── 反思循环主入口（严格照抄 TDD §5.2 伪代码） ───────────

async def run_deep_agent(
    query: str,
    thread_id: str,
    *,
    require_pdf: bool | None = None,
    required_sources: set[str] | None = None,
    on_event: Callable[[str, str, dict], None] | None = None,
) -> str:
    """
    执行主智能体，含"规划 → 执行 → 反思 → 纠错"循环（TDD §5.2）。

    Args:
        query: 用户任务描述。
        thread_id: 会话标识。
        require_pdf: 是否要求产出 PDF；None 时从 query 推断。
        required_sources: 应当调用的数据源集合；None 时从 query 推断。
        on_event: 可选事件回调 (event_type, message, data)，供接入层推送。

    Returns:
        最终报告文本；若达上限仍未通过，附未达标原因。
    """
    # 1. 会话上下文：设置 thread_id 与 session 目录
    set_session(thread_id)

    # 2. 创建工作目录 + 复制上传文件 + 生成工作环境指令
    env_instruction = _prepare_workspace(thread_id)
    if on_event:
        on_event("session_created", "会话工作目录已创建", {"path": str(get_session_dir())})

    # 校验上下文
    # require_pdf：缺省时从 query 推断
    _require_pdf = require_pdf if require_pdf is not None else infer_require_pdf(query)
    # required_sources：显式传入优先；否则等第 1 轮规划阶段主智能体的显式声明
    _required_sources: set[str] | None = required_sources
    logger.info(
        f"校验上下文：require_pdf={_require_pdf}, "
        f"required_sources={sorted(_required_sources) if _required_sources is not None else '待主智能体声明'}"
    )

    agent = build_main_agent()
    current_query = query + env_instruction
    result = ""          # 主智能体对话回复（可能只是"已成功创建 xx"）
    report_text = ""     # 用于校验与返回的报告正文（优先取自报告文件）
    hard_issues: list[str] = []
    soft_issue: str | None = None

    try:
        # 3. 反思循环
        round_num = 0
        while round_num < MAX_ROUNDS:
            round_num += 1
            logger.info(f"===== 反思循环第 {round_num}/{MAX_ROUNDS} 轮 =====")

            # 3.1 规划 + 执行（由主智能体完成，提示词驱动）
            round_result = await main_agent_run(
                agent, current_query, round_num, on_event=on_event
            )
            result = round_result.reply_text

            # 3.1.1 锁定本轮校验所需的数据源集合（首次规划阶段确定后不再变）
            if _required_sources is None:
                declared = _extract_declared_sources(round_result.ai_texts)
                if declared is not None:
                    _required_sources = declared
                else:
                    _required_sources = infer_required_sources(query)
                    logger.info(
                        f"未发现数据源声明，回退关键词推断：{sorted(_required_sources) or '空'}"
                    )

            # 3.1.2 收集报告正文（优先取工作目录的报告文件，问题一 · 方案 A）
            report_text = _collect_report_text(result)

            # 3.2 硬校验（程序级）
            hard_issues = hard_check(
                report_text,
                require_pdf=_require_pdf,
                produced_files=_collect_produced_files(),
                required_sources=_required_sources,
                called_sources=round_result.called_sources,
            )

            # 3.3 软校验（提示词驱动，仅在硬校验通过时执行）
            soft_issue = None
            if not hard_issues:
                soft_issue = await soft_review(report_text)

            # 3.4 判定
            if not hard_issues and not soft_issue:
                logger.info(f"第 {round_num} 轮通过全部校验，返回结果")
                if on_event:
                    on_event("task_result", "任务完成", {"result": report_text})
                return report_text

            # 3.5 生成纠错指令，注入下一轮
            logger.warning(
                f"第 {round_num} 轮未通过：hard={hard_issues}, soft={soft_issue}"
            )
            current_query = build_correction_prompt(query, hard_issues, soft_issue) + env_instruction

        # 4. 达到上限仍未通过：返回最优结果 + 未达标原因
        reasons = list(hard_issues)
        if soft_issue:
            reasons.append(soft_issue)

        logger.warning(f"达到 {MAX_ROUNDS} 轮上限仍未达标：{reasons}")
        final = (
            (report_text or result)
            + f"\n\n---\n【说明】本报告经 {MAX_ROUNDS} 轮反思仍未完全达标，未达标原因：\n"
            + "\n".join(f"- {r}" for r in reasons)
        )
        if on_event:
            on_event("task_result", "任务完成（未完全达标）", {"result": final})
        return final

    except Exception as e:
        logger.error(f"run_deep_agent 执行异常：{e}")
        if on_event:
            on_event("error", f"执行失败：{e}", {})
        return f"任务执行失败：{e}"

    finally:
        # ContextVar 在 finally 重置（TDD §11 可靠性）
        reset_session()


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    result = asyncio.run(
        run_deep_agent("列出所有抗生素类药品", thread_id="cli_test")
    )
    print(result)
