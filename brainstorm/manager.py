"""BrainstormManager — 编排核心，连接 strategies + interleave + convergence"""

from __future__ import annotations
import time
from typing import AsyncGenerator

from brainstorm.types import BrainstormSession, BrainstormEvent
from brainstorm.registry import create_tool, check_cli
from brainstorm.strategies import STRATEGIES


class BrainstormManager:
    """
    头脑风峰会话管理器。

    用法：
        manager = BrainstormManager(agents=("claude", "gemini"), strategy="debate", opts={...})
        async for event in manager.run():
            print(event)
    """

    def __init__(
        self,
        query: str,
        agents: tuple[str, str] = ("claude", "gemini"),
        strategy: str = "quick",
        opts: dict | None = None,
    ):
        self.query = query
        self.agent_a_id = agents[0]
        self.agent_b_id = agents[1]
        self.strategy = strategy
        self.opts = opts or {}

        self.session = BrainstormSession(
            query=query,
            strategy=strategy,  # type: ignore
            agents=[self.agent_a_id, self.agent_b_id],
        )

    async def run(self) -> AsyncGenerator[BrainstormEvent, None]:
        """执行头脑风暴，流式产出事件"""
        start_time = time.time()

        # 验证策略
        if self.strategy not in STRATEGIES:
            yield BrainstormEvent(
                type="agent_error",
                content=f"未知策略: {self.strategy}。可用: {list(STRATEGIES.keys())}",
            )
            return

        # 验证 Agent
        for aid in [self.agent_a_id, self.agent_b_id]:
            if aid not in ("claude", "gemini"):
                yield BrainstormEvent(
                    type="agent_error",
                    content=f"未知 Agent: {aid}。可用: claude, gemini",
                )
                return

        # 检查 CLI 是否安装
        for aid in [self.agent_a_id, self.agent_b_id]:
            installed, msg = check_cli(aid)
            if not installed:
                yield BrainstormEvent(type="agent_error", content=msg)
                return

        # 创建 Agent 工具
        try:
            agent_a = create_tool(self.agent_a_id)
            agent_b = create_tool(self.agent_b_id)
        except Exception as e:
            yield BrainstormEvent(type="agent_error", content=f"创建 Agent 失败: {e}")
            return

        try:
            # 选择综合 Agent（默认用第一个），另一个作为 fallback
            synthesis_agent = agent_a
            fallback_agent = agent_b

            # 全局超时（默认 300 秒，对标 TS 的 PROCESS_TIMEOUT_MS）
            global_timeout = self.opts.get("global_timeout", 300)

            # 执行策略（带全局超时）
            strategy_fn = STRATEGIES[self.strategy]
            async for event in self._run_with_timeout(
                strategy_fn, global_timeout,
                self.session, agent_a, agent_b, synthesis_agent, fallback_agent, self.opts,
            ):
                yield event

        except Exception as e:
            yield BrainstormEvent(type="agent_error", content=f"执行错误: {e}")

        finally:
            # 清理 Agent 资源
            try:
                agent_a.close()
            except Exception:
                pass
            try:
                agent_b.close()
            except Exception:
                pass

        # 最终事件
        elapsed = time.time() - start_time

        # 收集用量
        usage = {}
        for aid, resp in self.session.agent_responses.items():
            if resp.usage:
                usage[aid] = resp.usage
        usage["total_seconds"] = round(elapsed, 1)

        yield BrainstormEvent(
            type="done",
            strategy=self.strategy,
            usage=usage,
            elapsed_seconds=elapsed,
        )

    async def _run_with_timeout(self, strategy_fn, timeout, *args):
        """包装策略执行，添加全局超时"""
        start = time.time()
        async for event in strategy_fn(*args):
            if time.time() - start > timeout:
                yield BrainstormEvent(
                    type="agent_error",
                    content=f"⏰ 会话全局超时（{timeout}秒）。使用 --timeout 或 global_timeout 调整。",
                )
                return
            yield event
