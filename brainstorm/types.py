"""数据结构定义"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import uuid
import time

# ─── 类型别名 ────────────────────────────────────────────────────────────────

AgentId = Literal["claude", "gemini"]
Strategy = Literal["quick", "debate", "red-team", "perspectives", "delphi"]
Phase = Literal["initial", "individual", "discussion", "synthesis", "complete"]
DiscussionRole = Literal[
    "critic", "defender",        # debate
    "proposer", "challenger",    # red-team
    "risk-analyst", "innovator", # perspectives
    "facilitator", "refiner",    # delphi
]
ConvergenceRec = Literal["continue", "converged", "stalled"]


# ─── 核心数据结构 ────────────────────────────────────────────────────────────

@dataclass
class AgentResponse:
    """单个 Agent 的响应"""
    agent_id: str
    content: str = ""
    thinking: str = ""
    status: str = "pending"      # pending / streaming / complete / error
    usage: dict = field(default_factory=dict)


@dataclass
class ConvergenceMetrics:
    """收敛评估指标"""
    round: int
    agreement_count: int
    disagreement_count: int
    agreement_ratio: float
    position_stability: dict[str, float]    # agent_id → 相似度
    overall_convergence: float
    recommendation: ConvergenceRec


@dataclass
class DiscussionRound:
    """一轮讨论"""
    round_number: int
    contributions: dict[str, str]           # agent_id → 文本
    role_assignments: dict[str, str]        # agent_id → 角色
    convergence: ConvergenceMetrics | None = None


@dataclass
class BrainstormSession:
    """头脑风峰会话状态"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""
    phase: Phase = "initial"
    strategy: Strategy = "quick"
    agents: list[str] = field(default_factory=list)
    agent_responses: dict[str, AgentResponse] = field(default_factory=dict)
    discussion_rounds: list[DiscussionRound] = field(default_factory=list)
    convergence_history: list[ConvergenceMetrics] = field(default_factory=list)
    unified_solution: str = ""
    created_at: float = field(default_factory=time.time)


# ─── 流事件 ──────────────────────────────────────────────────────────────────

@dataclass
class BrainstormEvent:
    """头脑风暴流事件（yield 给调用者）"""
    type: str
    # ── 通用 ──
    content: str = ""
    # ── agent_text / agent_thinking / agent_complete / agent_error ──
    agent_id: str = ""
    # ── discussion_text / discussion_round_start ──
    discussion_role: str = ""
    round_number: int = 0
    # ── convergence_update ──
    convergence: ConvergenceMetrics | None = None
    # ── phase_change ──
    phase: str = ""
    # ── synthesis_text / synthesis_fallback ──
    # content
    # ── done ──
    usage: dict = field(default_factory=dict)       # {agent_id: {input_tokens, output_tokens}}
    elapsed_seconds: float = 0.0
    strategy: str = ""
