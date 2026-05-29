"""CLI 入口 — argparse 解析 + 主流程串联"""

from __future__ import annotations
import argparse
import asyncio
import sys
import os

from brainstorm.manager import BrainstormManager
from brainstorm.output import TerminalOutput, JsonOutput, SequentialOutput
from brainstorm.registry import available_agents, available_strategies, STRATEGY_INFO, get_style, check_cli
from brainstorm.config import load_config, show_config, create_default_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brainstorm",
        description="多 AI 头脑风暴工具 — 让两个 AI 协作讨论，产出更全面的建议",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  brainstorm "怎么设计这个 API 的缓存策略？"
  brainstorm "审查这段代码" -s red-team
  brainstorm "设计认证方案" -s debate -r 3
  brainstorm "优化查询" -s perspectives --json
  brainstorm "选型消息队列" -s delphi -a claude,gemini
  brainstorm --guide                    # 策略选择指南
  brainstorm --check                    # 检测环境
  brainstorm --show-config              # 查看配置
  echo "问题" | brainstorm              # 管道输入

策略说明:
  quick        并行分析，直接综合。速度最快。
  debate       结构化辩论，互相批评反驳。适合架构决策。
  red-team     一个提案，一个找漏洞。适合安全审查。
  perspectives 风险+创新双视角。适合平衡稳健和突破。
  delphi       主持人引导收敛到共识。适合复杂选型。
""",
    )

    # 位置参数
    parser.add_argument("query", nargs="?", help="要讨论的问题")

    # 策略
    parser.add_argument(
        "-s", "--strategy", default="quick",
        choices=available_strategies(),
        help="协作策略 (默认: quick)",
    )

    # Agent 选择
    parser.add_argument(
        "-a", "--agents", default="claude,gemini",
        help="两个 Agent，逗号分隔 (默认: claude,gemini)",
    )

    # 讨论轮数
    parser.add_argument(
        "-r", "--rounds", type=int, default=2,
        help="最大讨论轮数 (默认: 2)",
    )

    # 收敛控制
    parser.add_argument(
        "--no-converge", action="store_true",
        help="禁用自动收敛检测，强制跑满轮数",
    )

    # 输出格式
    parser.add_argument(
        "-j", "--json", action="store_true", dest="json_output",
        help="JSON 输出格式（供 Agent 解析）",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="安静模式，只输出最终综合结果",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="详细模式，显示所有中间过程",
    )
    parser.add_argument(
        "--sequential", action="store_true",
        help="顺序输出模式：先显示一个 agent 的完整输出，再显示另一个（避免交错碎片化）",
    )

    # 输出到文件
    parser.add_argument(
        "-o", "--output",
        help="输出到文件（默认: stdout）",
    )

    # 超时
    parser.add_argument(
        "--timeout", type=int, default=60,
        help="单个 Agent 静默超时秒数 (默认: 60)",
    )
    parser.add_argument(
        "--timeout-claude", type=int, default=0,
        help="Claude 专用超时秒数（覆盖 --timeout）",
    )
    parser.add_argument(
        "--timeout-gemini", type=int, default=0,
        help="Gemini 专用超时秒数（覆盖 --timeout）",
    )

    # 信息查询
    parser.add_argument(
        "--list-strategies", action="store_true",
        help="列出所有可用策略",
    )
    parser.add_argument(
        "--list-agents", action="store_true",
        help="列出所有可用 Agent",
    )

    # 环境检测
    parser.add_argument(
        "--check", action="store_true",
        help="检测 CLI 环境是否就绪",
    )

    # 配置管理
    parser.add_argument(
        "--show-config", action="store_true",
        help="显示当前生效配置",
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="创建默认配置文件 ~/.brainstorm/config.yaml",
    )
    parser.add_argument(
        "--guide", action="store_true",
        help="策略选择指南（新手推荐）",
    )

    return parser


def print_strategies():
    """列出可用策略"""
    print(f"\n{'策略':<15} {'名称':<12} 说明")
    print(f"{'─' * 55}")
    for sid in available_strategies():
        info = STRATEGY_INFO[sid]
        print(f"{sid:<15} {info['name']:<12} {info['description']}")
    print()


def print_agents():
    """列出可用 Agent"""
    print(f"\n{'Agent':<10} {'名称':<10} 说明")
    print(f"{'─' * 55}")
    for aid in available_agents():
        style = get_style(aid)
        print(f"{aid:<10} {style['display_name']:<10} {style.get('description', '')}")
    print()
def check_environment():
    """检测 CLI 环境"""
    print(f"\n🔍 环境检测")
    print(f"{'─' * 55}")
    all_ok = True
    for aid in available_agents():
        style = get_style(aid)
        installed, msg = check_cli(aid)
        if installed:
            print(f"  ✅ {style['icon']} {style['display_name']:<10} CLI 已安装")
        else:
            print(f"  ❌ {style['icon']} {style['display_name']:<10} CLI 未安装")
            print(f"     {msg}")
            all_ok = False
    print(f"{'─' * 55}")
    if all_ok:
        print("  ✅ 环境就绪，可以开始 brainstorm！")
    else:
        print("  ⚠️  部分 CLI 未安装，请按提示安装后重试。")
    print()


def print_guide():
    """策略选择指南"""
    print(f"""
{'═' * 55}
  📖 Brainstorm 策略选择指南
{'═' * 55}

  你的问题属于哪种类型？

  1️⃣  快速问题，只需要多个视角
     → 使用 quick（默认）
     示例: brainstorm "怎么优化这个函数？"

  2️⃣  架构/设计方案，需要深入讨论
     → 使用 debate
     示例: brainstorm "设计认证方案" -s debate

  3️⃣  安全审查，需要找漏洞
     → 使用 red-team
     示例: brainstorm "审查这段代码" -s red-team

  4️⃣  需要平衡风险和创新
     → 使用 perspectives
     示例: brainstorm "要不要迁移到微服务？" -s perspectives

  5️⃣  技术选型，需要达成共识
     → 使用 delphi
     示例: brainstorm "选消息队列：Kafka vs RabbitMQ？" -s delphi

{'─' * 55}
  💡 提示:
  • 不确定用什么？直接 brainstorm "你的问题"，会自动推荐
  • 用 -r 3 增加讨论轮数，获得更深入的分析
  • 用 --sequential 获得更清晰的终端输出
  • 用 --timeout 60 缩短等待时间
{'═' * 55}
""")



def recommend_strategy(query: str) -> str | None:
    """根据 query 内容推荐策略"""
    q = query.lower()
    # 审查/检查 → red-team
    if any(kw in q for kw in ["审查", "检查", "review", "audit", "安全", "漏洞", "security"]):
        return "red-team"
    # 选型/对比 → delphi
    if any(kw in q for kw in ["选型", "对比", "选择", "compare", "choose", "哪个好", "应该用"]):
        return "delphi"
    # 风险 → perspectives
    if any(kw in q for kw in ["风险", "risk", "权衡", "tradeoff"]):
        return "perspectives"
    # 设计/架构 → debate
    if any(kw in q for kw in ["设计", "架构", "design", "architecture", "方案"]):
        return "debate"
    return None


async def run_brainstorm(args: argparse.Namespace):
    """执行头脑风暴"""
    # 解析 agents
    agent_list = [a.strip() for a in args.agents.split(",")]
    if len(agent_list) != 2:
        print(f"错误: --agents 需要恰好两个 agent，用逗号分隔。收到: {agent_list}", file=sys.stderr)
        sys.exit(1)

    for aid in agent_list:
        if aid not in available_agents():
            print(f"错误: 未知 Agent '{aid}'。可用: {available_agents()}", file=sys.stderr)
            sys.exit(2)

    opts = {
        "rounds": args.rounds,
        "no_converge": args.no_converge,
        "timeout": args.timeout,
        "timeout_claude": args.timeout_claude or args.timeout,
        "timeout_gemini": args.timeout_gemini or args.timeout,
        "global_timeout": getattr(args, "global_timeout", 300),
    }

    manager = BrainstormManager(
        query=args.query,
        agents=(agent_list[0], agent_list[1]),
        strategy=args.strategy,
        opts=opts,
    )

    # 策略推荐提示（用户未显式指定策略时）
    recommended = recommend_strategy(args.query)
    if recommended and args.strategy == "quick" and not args.json_output:
        print(f"  💡 根据你的问题，推荐使用 {recommended} 策略。使用 -s {recommended} 指定。")

    # 输出头
    if not args.json_output and not args.quiet:
        style_a = get_style(agent_list[0])
        style_b = get_style(agent_list[1])
        strat = STRATEGY_INFO.get(args.strategy, {})
        print(f"\n{'═' * 55}")
        print(f"  {style_a['icon']} + {style_b['icon']}  BRAINSTORM  {strat.get('name', args.strategy)}")
        print(f"  Query: {args.query}")
        print(f"{'═' * 55}")

    # JSON 输出
    if args.json_output:
        json_out = JsonOutput()
        async for event in manager.run():
            json_out.collect(event, manager.session)

        result = json_out.flush()

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result)
            print(f"结果已写入: {args.output}", file=sys.stderr)
        else:
            print(result)
        return

    # 终端输出
    raw_mode = bool(args.output)
    if args.sequential:
        terminal = SequentialOutput(quiet=args.quiet, verbose=args.verbose)
    else:
        terminal = TerminalOutput(quiet=args.quiet, verbose=args.verbose, raw=raw_mode)

    if args.output:
        # 输出到文件时捕获 print 输出
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            async for event in manager.run():
                terminal.render(event)
        finally:
            captured = sys.stdout.getvalue()
            sys.stdout = old_stdout

        with open(args.output, "w", encoding="utf-8") as f:
            f.write(captured)
        print(f"结果已写入: {args.output}")
    else:
        async for event in manager.run():
            terminal.render(event)


def main():
    # 加载配置
    config = load_config()

    parser = build_parser()
    args = parser.parse_args()

    # 信息查询命令
    if args.list_strategies:
        print_strategies()
        return

    if args.list_agents:
        print_agents()
        return

    if args.check:
        check_environment()
        return

    if args.show_config:
        show_config(config)
        return

    if args.init_config:
        create_default_config()
        return

    if args.guide:
        print_guide()
        return

    # 用配置填充默认值（命令行参数优先）
    if args.strategy == "quick" and config.get("default_strategy") != "quick":
        args.strategy = config["default_strategy"]
    if args.agents == "claude,gemini" and config.get("default_agents"):
        agents = config["default_agents"]
        if isinstance(agents, list):
            args.agents = ",".join(agents)
    if args.rounds == 2 and config.get("default_rounds") != 2:
        args.rounds = config["default_rounds"]
    if args.timeout == 60 and config.get("timeout") != 60:
        args.timeout = config["timeout"]
    args.global_timeout = config.get("global_timeout", 300)

    # 必须提供 query（支持 stdin 管道）
    if not args.query:
        # 检查 stdin 是否有数据（管道输入）
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                args.query = stdin_data
        if not args.query:
            parser.print_help()
            sys.exit(1)

    try:
        asyncio.run(run_brainstorm(args))
    except KeyboardInterrupt:
        print("\n\n已中断", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
