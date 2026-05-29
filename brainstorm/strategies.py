"""5 种协作策略的提示词和流程实现"""

from __future__ import annotations
from typing import AsyncGenerator

from brainstorm.types import (
    BrainstormSession, BrainstormEvent, AgentResponse,
    DiscussionRound,
)
from brainstorm.interleave import interleave_with_timeout


def _is_timeout(event) -> bool:
    """检查事件是否是超时标记"""
    return isinstance(event, dict) and event.get("_timeout")
from brainstorm.convergence import assess_convergence
from brainstorm.registry import get_style


# ─── 工具函数 ────────────────────────────────────────────────────────────────

async def _collect_agent(tool, prompt: str) -> str:
    """调用 Agent，收集完整文本"""
    parts = []
    try:
        async for event in tool.send(prompt):
            if event.type == "text":
                parts.append(event.content)
            elif event.type == "error":
                return f"[错误: {event.content}]"
    except Exception as e:
        return f"[错误: {e}]"
    return "".join(parts)


async def _stream_agent(
    tool, prompt: str, agent_id: str, tag: str = "agent_text",
    round_number: int = 0, discussion_role: str = "",
) -> AsyncGenerator[BrainstormEvent, None]:
    """流式调用 Agent，yield BrainstormEvent"""
    try:
        async for event in tool.send(prompt):
            if event.type == "text":
                yield BrainstormEvent(
                    type=tag, content=event.content,
                    agent_id=agent_id, round_number=round_number,
                    discussion_role=discussion_role,
                )
            elif event.type == "error":
                yield BrainstormEvent(
                    type="agent_error", content=event.content, agent_id=agent_id,
                )
    except Exception as e:
        yield BrainstormEvent(
            type="agent_error", content=str(e), agent_id=agent_id,
        )


# ─── 公共阶段 ────────────────────────────────────────────────────────────────

async def _run_individual_phase(
    session: BrainstormSession, agent_a, agent_b,
    opts: dict, prompt_a: str | None = None, prompt_b: str | None = None,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Phase 1: 两个 Agent 并行分析。
    可以为每个 Agent 提供不同的 prompt（perspectives 模式需要）。
    """
    yield BrainstormEvent(type="phase_change", phase="individual")

    a_id = session.agents[0]
    b_id = session.agents[1]
    timeout = opts.get("timeout", 180)

    session.agent_responses[a_id] = AgentResponse(agent_id=a_id, status="streaming")
    session.agent_responses[b_id] = AgentResponse(agent_id=b_id, status="streaming")

    gen_a = _stream_agent(agent_a, prompt_a or session.query, a_id)
    gen_b = _stream_agent(agent_b, prompt_b or session.query, b_id)

    async for idx, event in interleave_with_timeout(gen_a, gen_b, timeout=timeout):
        aid = a_id if idx == 0 else b_id
        if _is_timeout(event):
            name = get_style(aid)["display_name"]
            session.agent_responses[aid].status = "error"
            yield BrainstormEvent(type="agent_error", agent_id=aid, content=f"{name} 响应超时")
            continue
        if event.type == "agent_text":
            session.agent_responses[aid].content += event.content
        elif event.type == "agent_error":
            session.agent_responses[aid].status = "error"
        yield event

    # 标记完成
    for aid in [a_id, b_id]:
        if session.agent_responses[aid].status == "streaming":
            session.agent_responses[aid].status = "complete"
        # 只有真正完成的 agent 才 emit agent_complete
        if session.agent_responses[aid].status == "complete":
            yield BrainstormEvent(type="agent_complete", agent_id=aid)


async def _run_synthesis_phase(
    session: BrainstormSession, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """Phase 3: 综合阶段（3 级降级：主 agent → 另一个 agent → 拼接）"""
    yield BrainstormEvent(type="phase_change", phase="synthesis")

    prompt = _build_synthesis_prompt(session)
    session.phase = "synthesis"

    # 第 1 级：主综合 agent
    synthesis_ok = False
    try:
        async for event in _stream_agent(synthesis_agent, prompt, session.agents[0], "synthesis_text"):
            if event.type == "synthesis_text":
                session.unified_solution += event.content
                synthesis_ok = True
                yield event  # ← 这行之前缺失！
            elif event.type == "agent_error":
                yield BrainstormEvent(type="agent_error", content=f"综合 agent 失败: {event.content}")
            else:
                yield event
    except Exception as e:
        yield BrainstormEvent(type="agent_error", content=f"综合 agent 异常: {e}")

    # 第 2 级：尝试另一个 agent
    if not session.unified_solution.strip() and fallback_agent:
        fallback_name = get_style(session.agents[1])["display_name"]
        yield BrainstormEvent(
            type="synthesis_fallback",
            content=f"主综合 agent 失败，尝试 {fallback_name}...",
        )
        session.unified_solution = ""
        try:
            async for event in _stream_agent(fallback_agent, prompt, session.agents[1], "synthesis_text"):
                if event.type == "synthesis_text":
                    session.unified_solution += event.content
                    yield event  # ← 同样补上
                elif event.type == "agent_error":
                    yield BrainstormEvent(type="agent_error", content=f"备用综合 agent 失败: {event.content}")
                else:
                    yield event
        except Exception as e:
            yield BrainstormEvent(type="agent_error", content=f"备用综合 agent 异常: {e}")

    # 第 3 级：拼接原始分析
    if not session.unified_solution.strip():
        fallback = _build_fallback_synthesis(session)
        session.unified_solution = fallback
        yield BrainstormEvent(type="synthesis_fallback", content="综合不可用，回退到原始分析")


# ─── 提示词构建 ──────────────────────────────────────────────────────────────

def _build_debate_critique_prompt(session: BrainstormSession, agent_id: str) -> str:
    """Debate: 批评提示词"""
    other_id = [a for a in session.agents if a != agent_id][0]
    other_content = session.agent_responses.get(other_id, AgentResponse("")).content
    other_name = get_style(other_id)["display_name"]

    return f"""# 结构化批评

你正在审查另一个 Agent 对某个编码/技术问题的分析。你的任务不是笼统地"提供想法"，而是进行一次聚焦的、结构化的批评，推动产生更好的解决方案。

## 原始查询

{session.query}

## 待批评的分析

### {other_name} 的分析

{other_content}

## 必需的响应格式

### 认同的观点
对于每个你同意的观点，说明它为什么正确以及有什么证据或推理支持它。要具体。

### 不认同的观点
对于每个你不同意的观点：
1. 引用具体的主张
2. 解释它为什么是错误的、不完整的或次优的
3. 提供你的替代方案及其推理

### 未经检验的假设
列出分析中未加论证的假设。对每个假设，解释它为什么重要。

### 遗漏的考量
原始查询中未被涉及的重要方面。

### 修正后的建议
根据你的批评，提供你的更新建议。要具体且可操作。"""


def _build_debate_rebuttal_prompt(
    session: BrainstormSession, agent_id: str, round_num: int,
) -> str:
    """Debate: 反驳提示词"""
    my_content = session.agent_responses.get(agent_id, AgentResponse("")).content
    other_id = [a for a in session.agents if a != agent_id][0]
    other_name = get_style(other_id)["display_name"]

    last_round = session.discussion_rounds[-1]
    other_critique = last_round.contributions.get(other_id, "")

    return f"""# 反驳 - 第 {round_num} 轮

你之前提供了一份分析，被另一个 Agent 批评了。审查他们的批评并做出回应。

## 原始查询

{session.query}

## 你的原始分析

{my_content}

## 收到的批评

### {other_name} 的批评

{other_critique}

## 必需的响应格式

### 让步的要点
批评中你接受的要点。对每个要点，说明你的建议将如何改变。

### 坚持的要点
你仍然认为正确的要点。提供额外的证据或推理。

### 精炼后的建议
你的更新建议，纳入了有效的批评要点。要具体。"""


def _build_red_team_challenge_prompt(session: BrainstormSession, proposer_id: str) -> str:
    """Red Team: 挑战提示词"""
    proposer_content = session.agent_responses.get(proposer_id, AgentResponse("")).content
    proposer_name = get_style(proposer_id)["display_name"]

    return f"""# 红队挑战

你的角色是找出提议方案中的每一个缺陷、风险和弱点。你不是在提供替代方案——你是在对这个方案进行压力测试。

## 原始查询

{session.query}

## {proposer_name} 的提议方案

{proposer_content}

## 必需的响应格式

### 安全与安全风险
识别安全漏洞、数据泄露风险或安全问题。

### 边界情况与失效模式
什么输入、状态或条件会导致这个方案失效？

### 可扩展性问题
在 10 倍或 100 倍规模下会发生什么？

### 维护负担
随着时间推移，什么会变得难以维护、测试或调试？

### 缺失的需求
原始查询中要求但这个方案未涉及的内容。

### 问题总结
对每个发现的问题，评定严重级别：
- **CRITICAL（严重）** — 必须在继续前修复
- **MAJOR（重要）** — 应该修复，存在显著风险
- **MINOR（次要）** — 最好修复，风险较低"""


def _build_red_team_defense_prompt(
    session: BrainstormSession, proposer_id: str, challenger_id: str,
) -> str:
    """Red Team: 防御提示词"""
    proposer_content = session.agent_responses.get(proposer_id, AgentResponse("")).content
    challenger_name = get_style(challenger_id)["display_name"]

    last_round = session.discussion_rounds[-1]
    challenge_content = last_round.contributions.get(challenger_id, "")

    return f"""# 防御与修订

你的提议方案受到了挑战。处理每个挑战点，在挑战者提出有效关注的地方修订你的方案。

## 原始查询

{session.query}

## 你的原始方案

{proposer_content}

## {challenger_name} 的挑战

{challenge_content}

## 必需的响应格式

### 接受的挑战
对每个有效的挑战，说明你将如何在修订方案中解决它。

### 拒绝的挑战
对每个你认为无效的挑战，用证据说明原因。

### 修订后的方案
你的更新方案，纳入了有效的挑战。标注相比原始方案的变化。"""


def _build_perspectives_risk_prompt(query: str) -> str:
    """Perspectives: 风险分析"""
    return f"""# 风险与正确性分析

通过 **可能出什么问题** 的视角分析以下查询。你的工作是团队的安全网——识别每一个风险、边界情况和潜在的失效模式。

## 查询

{query}

## 关注领域

分析以下适用的每个领域：
- **错误处理与边界情况** — 什么输入或状态可能导致失败？
- **安全影响** — 是否存在漏洞、注入风险或数据泄露？
- **性能瓶颈** — 在规模扩大时哪些操作可能变慢？
- **向后兼容性风险** — 这会破坏现有功能吗？
- **测试盲区** — 什么难以测试或容易被遗漏？
- **运维关注** — 部署风险、监控需求、回滚策略

## 必需格式

对每个识别的风险：
1. **风险**：清晰描述
2. **严重性**：高 / 中 / 低
3. **可能性**：高 / 中 / 低
4. **缓解措施**：如何应对

最后给出**风险总结**，按严重性排名前 3 大风险。"""


def _build_perspectives_innovator_prompt(query: str) -> str:
    """Perspectives: 创新分析"""
    return f"""# 机会与创新分析

通过 **最大价值和创意解决方案** 的视角分析以下查询。你的工作是推动团队走向最佳结果——寻找新颖的方法、快速胜利和面向未来的机会。

## 查询

{query}

## 关注领域

探索以下适用的每个领域：
- **新颖方法** — 更简单或更优雅的解决方案
- **可扩展性** — 如何设计以便将来易于变更
- **开发者体验** — 如何让开发工作更愉快
- **性能优化** — 让它更快或更高效的机会
- **其他领域的模式** — 其他领域适用的解决方案
- **快速胜利** — 高影响、低投入的改动

## 必需格式

对每个识别的机会：
1. **机会**：清晰描述
2. **影响**：高 / 中 / 低
3. **投入**：高 / 中 / 低
4. **方法**：如何实现

最后给出**推荐**，高亮影响投入比最高的前 3 个机会。"""


def _build_perspectives_cross_review_prompt(
    session: BrainstormSession, reviewer_id: str, other_id: str, role: str,
) -> str:
    """Perspectives: 交叉评审"""
    my_content = session.agent_responses.get(reviewer_id, AgentResponse("")).content
    other_content = session.agent_responses.get(other_id, AgentResponse("")).content
    other_name = get_style(other_id)["display_name"]

    if role == "risk-analyst":
        perspective = "你专注于风险。现在审查机会分析，识别创新想法在哪些地方应该受到风险关注的制约，或者你识别的风险在哪些地方已经被提议的方法所缓解。"
        title = "风险分析师评审机会"
    else:
        perspective = "你专注于机会。现在审查风险分析，识别风险在哪些地方被高估了，你的提议方法在哪些地方已经缓解了担忧，或者风险关注在哪些地方揭示了你推荐的重要约束。"
        title = "创新者评审风险"

    return f"""# 交叉评审：{title}

{perspective}

## 原始查询

{session.query}

## 你的分析

{my_content}

## {other_name} 的分析

{other_content}

## 必需的响应格式

### 他们的分析强化了你的观点的地方
来自另一个视角的补充或验证你发现的要点。

### 风险与机会冲突的地方
风险关注与创新想法之间的张力。对每个冲突，建议一条平衡的路径。

### 修正后的优先级
综合考虑两个视角后，你更新的前 3 项优先事项。"""


def _build_delphi_facilitator_prompt(session: BrainstormSession, round_num: int) -> str:
    """Delphi: 主持人汇总"""
    if round_num == 1:
        analyses = "\n\n---\n\n".join(
            r.content for r in session.agent_responses.values()
            if r.status == "complete"
        )
    else:
        last_round = session.discussion_rounds[-1]
        analyses = "\n\n---\n\n".join(last_round.contributions.values())

    return f"""# 主持人汇总 - 第 {round_num} 轮

你是一个公正的主持人，汇总团队的分析。不要添加你自己的意见或建议。你的工作是清晰地标识团队在哪些地方达成一致、在哪些地方存在分歧、以及哪些问题仍然悬而未决。

## 原始查询

{session.query}

## 团队分析

{analyses}

## 必需的响应格式

### 共识要点
两个 Agent 在哪些地方达成一致？对每个要点，评定信心等级：
- **Strong（强）** — 两个 Agent 明确同意，推理相似
- **Moderate（中）** — 两个 Agent 提议了相似的方法但理由不同
- **Tentative（弱）** — 隐含同意，未明确说明

### 分歧要点
两个 Agent 在哪些地方存在分歧？对每个分歧：
1. **立场 A**：……
2. **立场 B**：……
3. **核心张力**：为什么这个分歧重要

### 开放问题
如果得到回答，有助于解决分歧的问题。

### Convergence Score: ?/10
评定 Agent 之间的对齐程度（1 = 完全对立，10 = 几乎一致）。"""


def _build_delphi_refine_prompt(
    session: BrainstormSession, agent_id: str, facilitator_summary: str, round_num: int,
) -> str:
    """Delphi: 精炼提示词"""
    my_content = session.agent_responses.get(agent_id, AgentResponse("")).content

    return f"""# 精炼 - 第 {round_num} 轮

一位主持人汇总了团队的分析。审查该汇总并精炼你的建议。你应该在主持人识别出强共识的地方向共识靠拢，在存在分歧的地方阐明你的立场。

## 原始查询

{session.query}

## 你之前的分析

{my_content}

## 主持人汇总

{facilitator_summary}

## 必需的响应格式

### 立场变更
对每个你改变立场的要点，说明是什么说服了你。

### 坚持的立场
对你仍然坚持的分歧要点，提供额外的推理。

### 精炼后的建议
你的更新建议，纳入了主持人汇总中的见解。"""


def _build_synthesis_prompt(session: BrainstormSession) -> str:
    """综合阶段提示词（所有策略共用）"""
    agent_analyses = "\n\n---\n\n".join(
        f"## {get_style(aid)['display_name']} 的分析\n\n{resp.content}"
        for aid, resp in session.agent_responses.items()
        if resp.status == "complete"
    )

    discussions = ""
    for rnd in session.discussion_rounds:
        role_info = ", ".join(
            f"{get_style(aid)['display_name']}: {role}"
            for aid, role in rnd.role_assignments.items()
        )
        contributions = "\n\n".join(
            f"**{get_style(aid)['display_name']} ({rnd.role_assignments.get(aid, '')}):**\n\n{content}"
            for aid, content in rnd.contributions.items()
        )
        discussions += f"### 第 {rnd.round_number} 轮 — {role_info}\n\n{contributions}\n\n"

    convergence_info = ""
    if session.convergence_history:
        latest = session.convergence_history[-1]
        pct = round(latest.overall_convergence * 100)
        convergence_info = f"\n## 收敛状态：{latest.recommendation.upper()}\n总体收敛度：{pct}%\n"
        if latest.recommendation == "stalled":
            convergence_info += "Agent 未能达成完全共识，请特别关注未解决的分歧。\n"

    strategy_label = {
        "quick": "快速综合",
        "debate": "结构化辩论",
        "red-team": "红队分析",
        "perspectives": "双视角分析",
        "delphi": "德尔菲收敛",
    }

    label = strategy_label.get(session.strategy, session.strategy)

    prompt = f"""# 综合：最终团队建议

你正在综合一场 **{label}** 多智能体协作会话。你的工作不仅仅是合并——而是要产生一个比任何单个 Agent 的建议都更好的推荐。

## 原始查询

{session.query}

## Agent 分析

{agent_analyses}
"""

    if discussions:
        prompt += f"\n## 团队讨论\n\n{discussions}\n"

    if convergence_info:
        prompt += convergence_info + "\n"

    prompt += """## 必需的响应格式

### 执行摘要
一段话：应该做什么以及为什么。

### 达成一致的方案
团队达成共识的内容。引用两个 Agent 的具体观点。

### 已解决的分歧
讨论中解决的每个分歧：
- 矛盾点
- 解决方案
- 为什么这个解决方案是正确的

### 未解决的分歧
每个持续存在的分歧：
- 双方立场
- 推荐路径及明确的权衡说明

### 实施计划
按优先级排序的具体步骤。

### 风险缓解
讨论中发现的关键风险及其应对方法。"""

    return prompt


def _build_fallback_synthesis(session: BrainstormSession) -> str:
    """综合失败时的兜底方案"""
    parts = ["综合不可用，以下是各 Agent 的独立分析：\n"]
    for aid, resp in session.agent_responses.items():
        if resp.status == "complete" and resp.content.strip():
            name = get_style(aid)["display_name"]
            parts.append(f"### {name}\n\n{resp.content}\n")
    return "\n---\n\n".join(parts)


# ─── 5 种策略实现 ────────────────────────────────────────────────────────────

async def run_quick(
    session: BrainstormSession, agent_a, agent_b, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Quick 策略：并行分析 → 直接综合，无讨论。
    """
    session.phase = "individual"
    async for event in _run_individual_phase(session, agent_a, agent_b, opts):
        yield event

    session.phase = "synthesis"
    async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
        yield event

    session.phase = "complete"
    yield BrainstormEvent(type="phase_change", phase="complete")


async def run_debate(
    session: BrainstormSession, agent_a, agent_b, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Debate 策略：并行分析 → 批评/反驳轮 → 综合。
    """
    max_rounds = opts.get("rounds", 2)
    auto_converge = not opts.get("no_converge", False)
    timeout = opts.get("timeout", 180)

    # Phase 1: 独立分析
    session.phase = "individual"
    async for event in _run_individual_phase(session, agent_a, agent_b, opts):
        yield event

    # Guard: 少于 2 个 agent 完成 → 跳过讨论
    completed = sum(1 for r in session.agent_responses.values() if r.status == "complete")
    if completed < 2:
        session.phase = "synthesis"
        async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
            yield event
        session.phase = "complete"
        yield BrainstormEvent(type="phase_change", phase="complete")
        return

    # Phase 2: 讨论
    session.phase = "discussion"
    yield BrainstormEvent(type="phase_change", phase="discussion")

    a_id, b_id = session.agents

    for round_num in range(1, max_rounds + 1):
        yield BrainstormEvent(type="discussion_round_start", round_number=round_num)

        if round_num == 1:
            # 批评轮
            prompt_a = _build_debate_critique_prompt(session, a_id)
            prompt_b = _build_debate_critique_prompt(session, b_id)
            role = "critic"
        else:
            # 反驳轮
            prompt_a = _build_debate_rebuttal_prompt(session, a_id, round_num)
            prompt_b = _build_debate_rebuttal_prompt(session, b_id, round_num)
            role = "defender"

        contributions: dict[str, str] = {a_id: "", b_id: ""}
        gen_a = _stream_agent(
            agent_a, prompt_a, a_id, "discussion_text",
            round_number=round_num, discussion_role=role,
        )
        gen_b = _stream_agent(
            agent_b, prompt_b, b_id, "discussion_text",
            round_number=round_num, discussion_role=role,
        )

        async for idx, event in interleave_with_timeout(gen_a, gen_b, timeout=timeout):
            aid = a_id if idx == 0 else b_id
            if _is_timeout(event):
                name = get_style(aid)["display_name"]
                contributions[aid] = f"[{name} 响应超时]"
                yield BrainstormEvent(type="agent_error", agent_id=aid, content=f"{name} 响应超时")
                continue
            if event.type == "discussion_text":
                contributions[aid] += event.content
            yield event

        discussion_round = DiscussionRound(
            round_number=round_num,
            contributions=contributions,
            role_assignments={a_id: role, b_id: role},
        )
        session.discussion_rounds.append(discussion_round)

        # 收敛检测
        if auto_converge and round_num < max_rounds:
            convergence = assess_convergence(session.discussion_rounds, session.convergence_history)
            discussion_round.convergence = convergence
            session.convergence_history.append(convergence)
            yield BrainstormEvent(type="convergence_update", convergence=convergence)

            if convergence.recommendation in ("converged", "stalled"):
                break

    # Phase 3: 综合
    session.phase = "synthesis"
    async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
        yield event

    session.phase = "complete"
    yield BrainstormEvent(type="phase_change", phase="complete")


async def run_red_team(
    session: BrainstormSession, agent_a, agent_b, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Red Team 策略：提案方分析 → 挑战方找漏洞 → 提案方防御 → 综合。
    """
    timeout = opts.get("timeout", 180)
    proposer_id, challenger_id = session.agents

    # Phase 1: 只有提案方分析
    session.phase = "individual"
    yield BrainstormEvent(type="phase_change", phase="individual")

    proposer_ok = False
    session.agent_responses[proposer_id] = AgentResponse(agent_id=proposer_id, status="streaming")
    async for event in _stream_agent(agent_a, session.query, proposer_id):
        if event.type == "agent_text":
            session.agent_responses[proposer_id].content += event.content
            proposer_ok = True
        elif event.type == "agent_error":
            session.agent_responses[proposer_id].status = "error"
        yield event

    if not proposer_ok:
        session.agent_responses[proposer_id].status = "error"
        yield BrainstormEvent(type="agent_error", agent_id=proposer_id,
                              content=f"{get_style(proposer_id)['display_name']} 分析失败，跳过讨论阶段")
    else:
        session.agent_responses[proposer_id].status = "complete"
    yield BrainstormEvent(type="agent_complete", agent_id=proposer_id)

    # 提案方失败 → 跳过讨论，直接综合
    if not proposer_ok:
        session.phase = "synthesis"
        async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
            yield event
        session.phase = "complete"
        yield BrainstormEvent(type="phase_change", phase="complete")
        return

    # Phase 2: 挑战 + 防御（固定 2 轮）
    session.phase = "discussion"
    yield BrainstormEvent(type="phase_change", phase="discussion")

    # Round 1: 挑战
    yield BrainstormEvent(type="discussion_round_start", round_number=1)
    challenge_prompt = _build_red_team_challenge_prompt(session, proposer_id)
    contributions = {challenger_id: ""}

    async for event in _stream_agent(
        agent_b, challenge_prompt, challenger_id,
        "discussion_text", round_number=1, discussion_role="challenger",
    ):
        if event.type == "discussion_text":
            contributions[challenger_id] += event.content
        yield event

    session.discussion_rounds.append(DiscussionRound(
        round_number=1, contributions=contributions,
        role_assignments={proposer_id: "proposer", challenger_id: "challenger"},
    ))

    # Round 2: 防御
    yield BrainstormEvent(type="discussion_round_start", round_number=2)
    defense_prompt = _build_red_team_defense_prompt(session, proposer_id, challenger_id)
    contributions = {proposer_id: ""}

    async for event in _stream_agent(
        agent_a, defense_prompt, proposer_id,
        "discussion_text", round_number=2, discussion_role="defender",
    ):
        if event.type == "discussion_text":
            contributions[proposer_id] += event.content
        yield event

    session.discussion_rounds.append(DiscussionRound(
        round_number=2, contributions=contributions,
        role_assignments={proposer_id: "defender", challenger_id: "challenger"},
    ))

    # Phase 3: 综合
    session.phase = "synthesis"
    async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
        yield event

    session.phase = "complete"
    yield BrainstormEvent(type="phase_change", phase="complete")


async def run_perspectives(
    session: BrainstormSession, agent_a, agent_b, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Perspectives 策略：风险视角 + 创新视角并行 → 交叉评审 → 综合。
    """
    timeout = opts.get("timeout", 180)
    a_id, b_id = session.agents

    # Phase 1: 不同视角并行分析
    session.phase = "individual"
    risk_prompt = _build_perspectives_risk_prompt(session.query)
    innovator_prompt = _build_perspectives_innovator_prompt(session.query)

    async for event in _run_individual_phase(
        session, agent_a, agent_b, opts,
        prompt_a=risk_prompt, prompt_b=innovator_prompt,
    ):
        yield event

    # Phase 2: 交叉评审
    session.phase = "discussion"
    yield BrainstormEvent(type="phase_change", phase="discussion")
    yield BrainstormEvent(type="discussion_round_start", round_number=1)

    review_a_prompt = _build_perspectives_cross_review_prompt(session, a_id, b_id, "risk-analyst")
    review_b_prompt = _build_perspectives_cross_review_prompt(session, b_id, a_id, "innovator")

    contributions: dict[str, str] = {a_id: "", b_id: ""}
    gen_a = _stream_agent(
        agent_a, review_a_prompt, a_id,
        "discussion_text", round_number=1, discussion_role="risk-analyst",
    )
    gen_b = _stream_agent(
        agent_b, review_b_prompt, b_id,
        "discussion_text", round_number=1, discussion_role="innovator",
    )

    async for idx, event in interleave_with_timeout(gen_a, gen_b, timeout=timeout):
        aid = a_id if idx == 0 else b_id
        if _is_timeout(event):
            name = get_style(aid)["display_name"]
            contributions[aid] = f"[{name} 响应超时]"
            yield BrainstormEvent(type="agent_error", agent_id=aid, content=f"{name} 响应超时")
            continue
        if event.type == "discussion_text":
            contributions[aid] += event.content
        yield event

    session.discussion_rounds.append(DiscussionRound(
        round_number=1, contributions=contributions,
        role_assignments={a_id: "risk-analyst", b_id: "innovator"},
    ))

    # Phase 3: 综合
    session.phase = "synthesis"
    async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
        yield event

    session.phase = "complete"
    yield BrainstormEvent(type="phase_change", phase="complete")


async def run_delphi(
    session: BrainstormSession, agent_a, agent_b, synthesis_agent, fallback_agent, opts: dict,
) -> AsyncGenerator[BrainstormEvent, None]:
    """
    Delphi 策略：并行分析 → 主持人汇总 → Agent 精炼 → 综合。
    """
    max_rounds = opts.get("rounds", 2)
    auto_converge = not opts.get("no_converge", False)
    timeout = opts.get("timeout", 180)
    a_id, b_id = session.agents

    # Phase 1: 独立分析
    session.phase = "individual"
    async for event in _run_individual_phase(session, agent_a, agent_b, opts):
        yield event

    # Phase 2: 主持人 + 精炼
    session.phase = "discussion"
    yield BrainstormEvent(type="phase_change", phase="discussion")

    for round_num in range(1, max_rounds + 1):
        # 子轮 A: 主持人汇总（由 synthesis_agent 担任主持人）
        yield BrainstormEvent(type="discussion_round_start", round_number=round_num * 2 - 1)
        facilitator_prompt = _build_delphi_facilitator_prompt(session, round_num)
        facilitator_text = await _collect_agent(synthesis_agent, facilitator_prompt)

        # 找到 synthesis_agent 对应的 agent_id（用于 event 归属）
        facilitator_agent_id = a_id  # 默认用第一个 agent

        yield BrainstormEvent(
            type="discussion_text", content=facilitator_text,
            agent_id=facilitator_agent_id, round_number=round_num * 2 - 1,
            discussion_role="facilitator",
        )

        session.discussion_rounds.append(DiscussionRound(
            round_number=round_num * 2 - 1,
            contributions={facilitator_agent_id: facilitator_text},
            role_assignments={facilitator_agent_id: "facilitator"},
        ))

        # 子轮 B: Agent 精炼
        yield BrainstormEvent(type="discussion_round_start", round_number=round_num * 2)
        refine_a_prompt = _build_delphi_refine_prompt(session, a_id, facilitator_text, round_num)
        refine_b_prompt = _build_delphi_refine_prompt(session, b_id, facilitator_text, round_num)

        contributions: dict[str, str] = {a_id: "", b_id: ""}
        gen_a = _stream_agent(
            agent_a, refine_a_prompt, a_id,
            "discussion_text", round_number=round_num * 2, discussion_role="refiner",
        )
        gen_b = _stream_agent(
            agent_b, refine_b_prompt, b_id,
            "discussion_text", round_number=round_num * 2, discussion_role="refiner",
        )

        async for idx, event in interleave_with_timeout(gen_a, gen_b, timeout=timeout):
            aid = a_id if idx == 0 else b_id
            if _is_timeout(event):
                name = get_style(aid)["display_name"]
                contributions[aid] = f"[{name} 响应超时]"
                yield BrainstormEvent(type="agent_error", agent_id=aid, content=f"{name} 响应超时")
                continue
            if event.type == "discussion_text":
                contributions[aid] += event.content
            yield event

        session.discussion_rounds.append(DiscussionRound(
            round_number=round_num * 2,
            contributions=contributions,
            role_assignments={a_id: "refiner", b_id: "refiner"},
        ))

        # 更新 agent_responses 为精炼后的内容
        for aid in [a_id, b_id]:
            if contributions[aid].strip():
                session.agent_responses[aid].content = contributions[aid]

        # 收敛检测（对标 Mysti：converged 和 stalled 都提前结束）
        if auto_converge and round_num < max_rounds:
            convergence = assess_convergence(
                session.discussion_rounds, session.convergence_history,
                facilitator_text=facilitator_text,
            )
            session.convergence_history.append(convergence)
            yield BrainstormEvent(type="convergence_update", convergence=convergence)

            if convergence.recommendation in ("converged", "stalled"):
                break

    # Phase 3: 综合
    session.phase = "synthesis"
    async for event in _run_synthesis_phase(session, synthesis_agent, fallback_agent, opts):
        yield event

    session.phase = "complete"
    yield BrainstormEvent(type="phase_change", phase="complete")


# ─── 策略注册表 ──────────────────────────────────────────────────────────────

STRATEGIES: dict[str, callable] = {
    "quick": run_quick,
    "debate": run_debate,
    "red-team": run_red_team,
    "perspectives": run_perspectives,
    "delphi": run_delphi,
}
