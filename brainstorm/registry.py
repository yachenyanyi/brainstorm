"""Agent 注册表 — 根据 agent_id 创建对应的 Tool 实例"""

from __future__ import annotations
import shutil
import sys
import os

from brainstorm.claude_tool import ClaudeTool
from brainstorm.gemini_tool import GeminiTool


# ─── Agent 元数据 ─────────────────────────────────────────────────────────────

AGENT_STYLES: dict[str, dict] = {
    "claude": {
        "display_name": "Claude",
        "icon": "🟣",
        "color": "\033[35m",      # 紫色
        "description": "Anthropic Claude — 深度推理，结构化分析",
    },
    "gemini": {
        "display_name": "Gemini",
        "icon": "🔵",
        "color": "\033[34m",      # 蓝色
        "description": "Google Gemini — 快速响应，多模态能力",
    },
}

STRATEGY_INFO: dict[str, dict] = {
    "quick": {
        "name": "快速综合",
        "description": "两个 Agent 并行分析，直接合并。速度最快，适合简单问题。",
    },
    "debate": {
        "name": "结构化辩论",
        "description": "两个 Agent 互相批评和反驳，推动更好的方案。适合架构决策。",
    },
    "red-team": {
        "name": "红队审查",
        "description": "一个 Agent 提案，另一个压力测试找漏洞。适合安全审查和方案验证。",
    },
    "perspectives": {
        "name": "双视角分析",
        "description": "一个分析风险，一个探索创新。适合需要平衡稳健和突破的场景。",
    },
    "delphi": {
        "name": "德尔菲共识",
        "description": "主持人引导 Agent 逐步收敛到共识。适合复杂决策和选型。",
    },
}


# ─── CLI 安装提示 ────────────────────────────────────────────────────────────

CLI_INSTALL_HINTS: dict[str, dict] = {
    "claude": {
        "cli_name": "claude",
        "install_cmd": "npm install -g @anthropic-ai/claude-code",
        "auth_hint": "请先运行 'claude' 完成登录认证",
    },
    "gemini": {
        "cli_name": "gemini",
        "install_cmd": "npm install -g @google/gemini-cli",
        "auth_hint": "请先运行 'gemini' 完成登录认证",
    },
}


def check_cli(agent_id: str) -> tuple[bool, str]:
    """
    检查 CLI 是否安装。

    Returns:
        (installed, message) — installed=True 时 message 为空，否则为友好错误信息。
    """
    info = CLI_INSTALL_HINTS.get(agent_id)
    if not info:
        return False, f"未知 Agent: {agent_id}"

    cli_path = shutil.which(info["cli_name"])
    if cli_path:
        return True, ""

    return False, (
        f"❌ 未找到 {info['cli_name']} CLI。\n"
        f"   安装命令: {info['install_cmd']}\n"
        f"   {info['auth_hint']}"
    )


def create_tool(agent_id: str):
    """根据 agent_id 创建工具实例"""
    if agent_id == "claude":
        return ClaudeTool(use_persistent=False, permission_mode="plan")
    elif agent_id == "gemini":
        return GeminiTool(permission_mode="bypass")
    else:
        raise ValueError(f"未知的 Agent: {agent_id}。可用: {list(AGENT_STYLES.keys())}")


def get_style(agent_id: str) -> dict:
    """获取 Agent 的显示样式"""
    return AGENT_STYLES.get(agent_id, {
        "display_name": agent_id,
        "icon": "⚪",
        "color": "",
        "description": "",
    })


def available_agents() -> list[str]:
    """列出所有可用的 Agent ID"""
    return list(AGENT_STYLES.keys())


def available_strategies() -> list[str]:
    """列出所有可用的策略"""
    return list(STRATEGY_INFO.keys())
