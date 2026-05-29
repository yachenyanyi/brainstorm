"""输出格式化 — 终端彩色输出 + JSON 格式化"""

from __future__ import annotations
import json
import re
import sys
import time
import threading

from brainstorm.types import BrainstormEvent, BrainstormSession
from brainstorm.registry import get_style


# ─── ANSI 颜色 ───────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"


# ─── 进度 Spinner ────────────────────────────────────────────────────────────

class Spinner:
    """后台线程 spinner，显示等待进度"""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._active = False
        self._thread: threading.Thread | None = None
        self._label = ""
        self._start_time = 0.0

    def start(self, label: str = ""):
        if self._active:
            return
        self._active = True
        self._label = label
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._active = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        # 确保清除 spinner 行
        try:
            sys.stderr.write("\r\033[K")  # ANSI: 光标到行首 + 清除行
            sys.stderr.flush()
        except Exception:
            pass

    def _run(self):
        # 延迟 0.5 秒再开始显示，避免快速响应时闪烁
        import time as _time
        _time.sleep(0.5)
        if not self._active:
            return
        i = 0
        while self._active:
            frame = self.FRAMES[i % len(self.FRAMES)]
            elapsed = time.time() - self._start_time
            line = f"  {frame} {self._label} ({elapsed:.0f}s)"
            sys.stderr.write(f"\r{line}")
            sys.stderr.flush()
            _time.sleep(0.1)
            i += 1


# ─── 终端输出 ────────────────────────────────────────────────────────────────

class TerminalOutput:
    """彩色终端输出，适合人在终端直接看"""

    _ANSI_RE = re.compile(r'\033\[[0-9;]*m')

    @staticmethod
    def strip_ansi(text: str) -> str:
        """去除 ANSI 颜色码"""
        return TerminalOutput._ANSI_RE.sub('', text)

    @staticmethod
    def format_markdown(text: str) -> str:
        """简单 markdown → 终端格式化"""
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # H1
            if stripped.startswith('# ') and not stripped.startswith('## '):
                result.append(f"\n{BOLD}{CYAN}{stripped[2:]}{RESET}")
            # H2
            elif stripped.startswith('## '):
                result.append(f"\n{BOLD}{stripped[3:]}{RESET}")
            # H3
            elif stripped.startswith('### '):
                result.append(f"\n{BOLD}{stripped[4:]}{RESET}")
            # Horizontal rule
            elif stripped in ('---', '***', '___'):
                result.append(f"  {'─' * 50}")
            # Bold text (**text**)
            elif '**' in stripped:
                import re
                line_fmt = re.sub(r'\*\*(.+?)\*\*', f'{BOLD}\\1{RESET}', stripped)
                result.append(f"  {line_fmt}")
            # Bullet points
            elif stripped.startswith('- ') or stripped.startswith('* '):
                result.append(f"  • {stripped[2:]}")
            # Numbered lists
            elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in '.):':
                result.append(f"  {stripped}")
            # Code blocks
            elif stripped.startswith('```'):
                pass  # skip code fence markers
            # Blockquote
            elif stripped.startswith('> '):
                result.append(f"  {YELLOW}│{RESET} {stripped[2:]}")
            # Table rows
            elif '|' in stripped and stripped.startswith('|'):
                # Skip table separator rows
                if all(c in '|-: ' for c in stripped):
                    continue
                cells = [c.strip() for c in stripped.split('|')[1:-1]]
                result.append(f"  {'  │  '.join(cells)}")
            # Regular text
            elif stripped:
                result.append(f"  {stripped}")
            else:
                result.append("")
        return '\n'.join(result)

    def __init__(self, quiet: bool = False, verbose: bool = False, raw: bool = False):
        self.quiet = quiet
        self.verbose = verbose
        self.raw = raw  # raw=True 时不输出颜色和 box 字符（适合写文件）
        self._current_agent: str = ""
        self._current_phase: str = ""
        self._buffer: dict[str, str] = {}
        self._synthesis_buf: list[str] = []
        self._spinner = Spinner() if not raw else None

    def _out(self, text: str = "", end: str = "\n", flush: bool = False):
        """统一输出，raw 模式下剥离 ANSI 和 box 字符"""
        if self.raw:
            text = self.strip_ansi(text)
            # 替换 box-drawing 字符
            text = text.replace('┌', '+').replace('─', '-').replace('│', '|')
            text = text.replace('└', '+').replace('═', '=')
        print(text, end=end, flush=flush)

    def render(self, event: BrainstormEvent):
        """根据事件类型渲染到终端"""
        t = event.type

        if t == "phase_change":
            self._render_phase(event.phase)

        elif t == "agent_text":
            if self._spinner:
                self._spinner.stop()
            if not self.quiet:
                self._render_agent_stream(event.agent_id, event.content)

        elif t == "agent_complete":
            if self._spinner:
                self._spinner.stop()
            if not self.quiet:
                self._close_agent_box()
                style = get_style(event.agent_id)
                self._out(f"  {style['icon']} {style['display_name']} 分析完成")

        elif t == "agent_error":
            if self._spinner:
                self._spinner.stop()
            self._out(f"  {RED}错误: {event.content}{RESET}")

        elif t == "discussion_round_start":
            if not self.quiet:
                self._close_agent_box()
                self._out(f"\n  {BOLD}第 {event.round_number} 轮 讨论{RESET}")
                self._out(f"  {'─' * 50}")

        elif t == "discussion_text":
            if not self.quiet:
                self._render_agent_stream(
                    event.agent_id, event.content,
                    suffix=f" ({event.discussion_role})" if event.discussion_role else "",
                )

        elif t == "convergence_update":
            if not self.quiet:
                c = event.convergence
                if c:
                    pct = round(c.overall_convergence * 100)
                    icon = "✓" if c.recommendation == "converged" else ("✗" if c.recommendation == "stalled" else "→")
                    color = GREEN if c.recommendation == "converged" else (RED if c.recommendation == "stalled" else YELLOW)
                    self._out(f"\n  {color}▸ 收敛: {pct}% ({c.recommendation}) {icon}{RESET}")

        elif t == "synthesis_text":
            if self._spinner:
                self._spinner.stop()
            self._close_agent_box()
            self._synthesis_buf.append(event.content)

        elif t == "synthesis_fallback":
            if self._spinner:
                self._spinner.stop()
            self._close_agent_box()
            self._flush_synthesis()
            self._out(f"\n  {YELLOW}{event.content}{RESET}\n")

        elif t == "done":
            self._flush_synthesis()
            if self._spinner:
                self._spinner.stop()
            self._render_done(event)

    def _close_agent_box(self):
        """关闭当前 Agent 的输出框"""
        if self._current_agent:
            self._out(f"\n  └{'─' * 50}")
            self._current_agent = ""

    def _flush_synthesis(self):
        """格式化输出缓冲的综合内容"""
        if not self._synthesis_buf:
            return
        raw_text = "".join(self._synthesis_buf)
        self._synthesis_buf.clear()
        formatted = self.format_markdown(raw_text)
        self._out(formatted)

    def _render_phase(self, phase: str):
        labels = {
            "individual": "Phase 1: 独立分析",
            "discussion": "Phase 2: 讨论",
            "synthesis": "Phase 3: 综合",
        }
        # complete 阶段由 done 事件处理，跳过
        if phase == "complete":
            return
        # 阶段切换时，确保关闭上一个 Agent 的 box 和 spinner
        if self._spinner:
            self._spinner.stop()
        self._close_agent_box()
        label = labels.get(phase, phase)
        if self.quiet and phase != "synthesis":
            return
        self._out(f"\n{'─' * 55}")
        self._out(f"  {BOLD}{CYAN}{label}{RESET}")
        self._out(f"{'─' * 55}\n")
        # 启动 spinner
        if self._spinner:
            phase_names = {"individual": "独立分析", "discussion": "讨论", "synthesis": "综合"}
            name = phase_names.get(phase, phase)
            self._spinner.start(f"等待 {name} 阶段响应...")

    def _render_agent_stream(self, agent_id: str, content: str, suffix: str = ""):
        """渲染 Agent 的流式输出，支持逐 token 追加打印"""
        style = get_style(agent_id)
        color = style.get("color", "")
        icon = style.get("icon", "")
        name = style.get("display_name", agent_id)

        # 如果 Agent 切换了，打印新的头部
        if self._current_agent != agent_id:
            if self._current_agent:
                self._out(f"\n  └{'─' * 50}")
            self._out(f"  ┌─ {icon} {BOLD}{color}{name}{RESET}{suffix}", end="")
            self._out(f"\n  │ ", end="")
            self._current_agent = agent_id
            self._buffer[agent_id] = ""

        # 流式打印：遇到换行时重置前缀
        for ch in content:
            if ch == "\n":
                self._out(f"\n  │ ", end="")
            else:
                self._out(ch, end="", flush=True)
        # flush already handled per-char

    def _render_done(self, event: BrainstormEvent):
        """渲染完成总结"""
        if self._current_agent:
            self._out(f"  └{'─' * 50}")

        self._out(f"\n{'═' * 55}")
        self._out(f"  {BOLD}完成{RESET}", end="")

        if event.elapsed_seconds:
            self._out(f"  耗时: {event.elapsed_seconds:.1f}s", end="")

        if event.usage:
            tokens = []
            for aid, u in event.usage.items():
                if isinstance(u, dict) and "input_tokens" in u:
                    total = u.get("input_tokens", 0) + u.get("output_tokens", 0)
                    tokens.append(f"{aid}={total}")
            if tokens:
                self._out(f"  Tokens: {', '.join(tokens)}", end="")

        self._out(f"\n{'═' * 55}")


# ─── 顺序输出（非交错） ─────────────────────────────────────────────────────

class SequentialOutput:
    """
    顺序输出：先收集完一个 agent 的全部输出，再整体显示。
    解决终端 interleave 碎片化问题。
    """

    _ANSI_RE = re.compile(r'\033\[[0-9;]*m')

    def __init__(self, quiet: bool = False, verbose: bool = False):
        self.quiet = quiet
        self.verbose = verbose
        # 缓冲：agent_id → 文本列表
        self._buffers: dict[str, list[str]] = {}
        self._current_phase: str = ""
        self._phase_label: dict[str, str] = {
            "individual": "Phase 1: 独立分析",
            "discussion": "Phase 2: 讨论",
            "synthesis": "Phase 3: 综合",
        }

    def render(self, event: BrainstormEvent):
        t = event.type

        if t == "phase_change":
            self._flush_all()
            self._current_phase = event.phase
            if event.phase == "complete":
                return
            if self.quiet and event.phase != "synthesis":
                return
            label = self._phase_label.get(event.phase, event.phase)
            print(f"\n{'─' * 55}")
            print(f"  {BOLD}{CYAN}{label}{RESET}")
            print(f"{'─' * 55}\n")

        elif t == "agent_text":
            if not self.quiet:
                self._buffer(event.agent_id, event.content)

        elif t == "discussion_text":
            if not self.quiet:
                self._buffer(event.agent_id, event.content)

        elif t == "agent_complete":
            if not self.quiet:
                self._flush_agent(event.agent_id)
                style = get_style(event.agent_id)
                print(f"  {style['icon']} {style['display_name']} 分析完成")

        elif t == "agent_error":
            print(f"  ❌ 错误: {event.content}")

        elif t == "discussion_round_start":
            if not self.quiet:
                self._flush_all()
                print(f"\n  {BOLD}第 {event.round_number} 轮 讨论{RESET}")
                print(f"  {'─' * 50}")

        elif t == "convergence_update":
            if not self.quiet:
                c = event.convergence
                if c:
                    pct = round(c.overall_convergence * 100)
                    icon = "✓" if c.recommendation == "converged" else ("✗" if c.recommendation == "stalled" else "→")
                    color = GREEN if c.recommendation == "converged" else (RED if c.recommendation == "stalled" else YELLOW)
                    print(f"\n  {color}▸ 收敛: {pct}% ({c.recommendation}) {icon}{RESET}")

        elif t == "synthesis_text":
            self._flush_all()
            print(event.content, end="", flush=True)

        elif t == "synthesis_fallback":
            self._flush_all()
            print(f"\n  {YELLOW}{event.content}{RESET}\n")

        elif t == "done":
            self._flush_all()
            self._render_done(event)

    def _buffer(self, agent_id: str, content: str):
        if agent_id not in self._buffers:
            self._buffers[agent_id] = []
        self._buffers[agent_id].append(content)

    def _flush_agent(self, agent_id: str):
        """整体输出一个 agent 的缓冲内容"""
        parts = self._buffers.pop(agent_id, [])
        if not parts:
            return
        style = get_style(agent_id)
        color = style.get("color", "")
        icon = style.get("icon", "")
        name = style.get("display_name", agent_id)
        text = "".join(parts)
        print(f"  ┌─ {icon} {BOLD}{color}{name}{RESET}")
        for line in text.split("\n"):
            print(f"  │ {line}")
        print(f"  └{'─' * 50}")

    def _flush_all(self):
        """输出所有缓冲的 agent 内容"""
        for agent_id in list(self._buffers.keys()):
            self._flush_agent(agent_id)

    def _render_done(self, event: BrainstormEvent):
        print(f"\n{'═' * 55}")
        print(f"  {BOLD}完成{RESET}", end="")
        if event.elapsed_seconds:
            print(f"  耗时: {event.elapsed_seconds:.1f}s", end="")
        if event.usage:
            tokens = []
            for aid, u in event.usage.items():
                if isinstance(u, dict) and "input_tokens" in u:
                    total = u.get("input_tokens", 0) + u.get("output_tokens", 0)
                    tokens.append(f"{aid}={total}")
            if tokens:
                print(f"  Tokens: {', '.join(tokens)}", end="")
        print(f"\n{'═' * 55}")


# ─── JSON 输出 ───────────────────────────────────────────────────────────────

class JsonOutput:
    """
    JSON 输出，收集所有事件，最后一次性输出结构化 JSON。
    给 Agent 解析用。
    """

    def __init__(self):
        self._data: dict = {
            "query": "",
            "strategy": "",
            "agents": [],
            "phases": {
                "individual": {},
                "discussion": [],
                "synthesis": "",
            },
            "usage": {},
            "session_id": "",
            "elapsed_seconds": 0,
        }
        self._discussion_rounds: dict[int, dict] = {}

    def collect(self, event: BrainstormEvent, session: BrainstormSession):
        """收集事件到内部结构"""
        t = event.type

        if t == "phase_change":
            pass  # phase 信息从 session 获取

        elif t == "agent_text":
            resp = session.agent_responses.get(event.agent_id)
            if resp:
                if event.agent_id not in self._data["phases"]["individual"]:
                    self._data["phases"]["individual"][event.agent_id] = {
                        "content": "",
                        "status": "streaming",
                        "usage": {},
                    }
                self._data["phases"]["individual"][event.agent_id]["content"] += event.content

        elif t == "agent_complete":
            if event.agent_id in self._data["phases"]["individual"]:
                self._data["phases"]["individual"][event.agent_id]["status"] = "complete"

        elif t == "agent_error":
            aid = event.agent_id
            if aid:
                # 确保 agent 在 individual 数据中存在
                if aid not in self._data["phases"]["individual"]:
                    self._data["phases"]["individual"][aid] = {
                        "content": "",
                        "status": "error",
                        "usage": {},
                    }
                self._data["phases"]["individual"][aid]["status"] = "error"
                self._data["phases"]["individual"][aid]["error"] = event.content

        elif t == "discussion_round_start":
            if event.round_number not in self._discussion_rounds:
                self._discussion_rounds[event.round_number] = {
                    "round": event.round_number,
                    "role_assignments": {},
                    "contributions": {},
                    "convergence": None,
                }

        elif t == "discussion_text":
            rnd = self._discussion_rounds.get(event.round_number)
            if rnd:
                aid = event.agent_id or "facilitator"
                rnd["contributions"][aid] = (
                    rnd["contributions"].get(aid, "") + event.content
                )
                if event.discussion_role:
                    rnd["role_assignments"][aid] = event.discussion_role

        elif t == "convergence_update" and event.convergence:
            # 找最近的 round
            if self._discussion_rounds:
                last_round_num = max(self._discussion_rounds.keys())
                self._discussion_rounds[last_round_num]["convergence"] = {
                    "agreement_ratio": event.convergence.agreement_ratio,
                    "position_stability": event.convergence.position_stability,
                    "overall": event.convergence.overall_convergence,
                    "recommendation": event.convergence.recommendation,
                }

        elif t == "synthesis_text":
            self._data["phases"]["synthesis"] += event.content

        elif t == "synthesis_fallback":
            self._data["phases"]["synthesis"] = event.content

        elif t == "done":
            self._data["strategy"] = event.strategy
            self._data["elapsed_seconds"] = event.elapsed_seconds
            self._data["usage"] = event.usage
            self._data["session_id"] = session.id
            self._data["query"] = session.query
            self._data["agents"] = [
                {"id": aid, "display_name": get_style(aid)["display_name"]}
                for aid in session.agents
            ]

    def flush(self) -> str:
        """输出完整 JSON 字符串"""
        # 按 round 排序 discussion rounds
        self._data["phases"]["discussion"] = [
            self._discussion_rounds[k]
            for k in sorted(self._discussion_rounds.keys())
        ]
        return json.dumps(self._data, ensure_ascii=False, indent=2)
