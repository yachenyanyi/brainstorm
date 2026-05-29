"""
Gemini CLI Python 封装 - 可作为工具给其他 Agent 调用

用法:
  from gemini_tool import GeminiTool

  # 异步用法
  async with GeminiTool() as gemini:
      async for event in gemini.send("帮我写一个排序算法"):
          if event.type == "text":
              print(event.content, end="", flush=True)

  # 同步用法
  for event in GeminiTool().send_sync("hello"):
      print(event)
"""

import json
import os
import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator, Generator


# ─── 数据类型 ───────────────────────────────────────────────────────────────

@dataclass
class StreamEvent:
    """统一的流事件（与 ClaudeTool 兼容）"""
    type: str                   # text/tool_use/tool_result/session_active/error/done
    content: str = ""           # text / error 的内容
    name: str = ""              # tool_use 的工具名
    tool_id: str = ""           # tool_use / tool_result 的 ID
    input: dict = field(default_factory=dict)    # tool_use 的参数
    output: str = ""            # tool_result 的输出
    status: str = ""            # tool_use: running; tool_result: completed/failed
    session_id: str = ""        # session_active 时返回
    usage: dict = field(default_factory=dict)    # done 时的 token 用量


# ─── 主类 ────────────────────────────────────────────────────────────────────

class GeminiTool:
    """
    Gemini CLI 的 Python 封装。
    对标 Mysti 的 GeminiProvider 实现。
    Gemini 不支持常驻进程模式，只有单次模式。
    """

    def __init__(
        self,
        cli_path: str = "gemini",
        model: str | None = None,
        permission_mode: str = "bypass",           # bypass / sandbox
        session_id: str | None = None,
    ):
        self.cli_path = cli_path
        self.model = model or "gemini-2.5-flash"
        self.permission_mode = permission_mode
        self.session_id = session_id

        self._usage: dict = {}

    # ─── 上下文管理器 ──────────────────────────────────────────────────────

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass  # 单次模式，无需清理

    # ─── 公开 API ──────────────────────────────────────────────────────────

    async def send(self, prompt: str) -> AsyncGenerator[StreamEvent, None]:
        """发送消息，异步生成器产出 StreamEvent"""
        async for event in self._send_single_shot(prompt):
            yield event

    def send_sync(self, prompt: str) -> Generator[StreamEvent, None, None]:
        """同步版本"""
        loop = asyncio.new_event_loop()
        try:
            async_gen = self.send(prompt)
            while True:
                try:
                    event = loop.run_until_complete(async_gen.__anext__())
                    yield event
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    @property
    def last_usage(self) -> dict:
        return self._usage

    @property
    def current_session_id(self) -> str | None:
        return self.session_id

    def close(self):
        """同步关闭（兼容 ClaudeTool 接口）。Gemini 单次模式无需清理。"""
        pass

    # ─── 单次模式 ──────────────────────────────────────────────────────────

    async def _send_single_shot(self, prompt: str) -> AsyncGenerator[StreamEvent, None]:
        """
        单次模式：spawn → 写 prompt → 读流 → 杀进程。
        Gemini 不支持 --input-format stream-json，只有单次模式。
        对标 GeminiProvider.buildCliArgs()
        """
        args = self._build_args()

        env = os.environ.copy()
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

        proc = await asyncio.create_subprocess_exec(
            self.cli_path, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # 写入 prompt 后关闭 stdin
        proc.stdin.write(prompt.encode())
        proc.stdin.close()

        try:
            async for line in self._readlines(proc.stdout):
                event = self._parse_line(line)
                if event:
                    yield event

            # 等待进程退出
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()

            if proc.returncode and proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode(errors="replace")
                # 提取友好错误信息
                friendly = self._extract_error(stderr)
                if friendly:
                    yield StreamEvent(type="error", content=friendly)
                elif stderr.strip():
                    # 过滤诊断噪音
                    stderr_clean = "\n".join(
                        l for l in stderr.splitlines()
                        if not l.startswith("Ripgrep") and not l.startswith("Attempt")
                    )
                    if stderr_clean.strip():
                        yield StreamEvent(type="error", content=stderr_clean.strip()[:200])

        finally:
            try:
                proc.kill()
            except Exception:
                pass

        yield StreamEvent(type="done", usage=self._usage)

    def _build_args(self) -> list[str]:
        """
        构建 CLI 参数。
        对标 GeminiProvider.buildCliArgs()
        """
        args = ["--output-format", "stream-json"]

        # 模型
        if self.model:
            args.extend(["-m", self.model])

        # 权限标志
        args.extend(self._permission_flags())

        # Session 恢复
        if self.session_id:
            args.extend(["--resume", self.session_id])

        return args

    # ─── 权限标志 ──────────────────────────────────────────────────────────

    def _permission_flags(self) -> list[str]:
        """
        对标 GeminiProvider._addPermissionFlags()
        """
        if self.permission_mode == "sandbox":
            return ["--sandbox"]
        return ["--yolo"]

    # ─── 异步逐行读取 ──────────────────────────────────────────────────────

    async def _readlines(self, stream: asyncio.streams.StreamReader) -> AsyncGenerator[str, None]:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                yield text

    # ─── 流解析 ────────────────────────────────────────────────────────────

    def _parse_line(self, line: str) -> StreamEvent | None:
        """
        解析 Gemini CLI 输出的单行 JSON。
        对标 GeminiProvider.parseStreamLine() (line 284-399)

        事件类型:
          init         → session_active
          message      → text (role=assistant, delta=false 为完整消息)
          tool_use     → tool_use
          tool_result  → tool_result
          result       → done (捕获用量)
          error        → error
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # 过滤诊断噪音和错误堆栈
            trimmed = line.strip()
            if trimmed and not self._is_diagnostic(trimmed) and not self._is_error_trace(trimmed):
                return StreamEvent(type="text", content=line)
            return None

        event_type = data.get("type", "")

        # ── init ──
        if event_type == "init":
            sid = data.get("session_id")
            if sid and not self.session_id:
                self.session_id = sid
                return StreamEvent(type="session_active", session_id=sid)
            return None

        # ── message ──
        if event_type == "message":
            role = data.get("role", "")
            content = data.get("content", "")

            # 只处理 assistant 消息（跳过 user echo）
            if role == "assistant" and content:
                return StreamEvent(type="text", content=content)

            return None

        # ── tool_use ──
        if event_type == "tool_use":
            tool_name = data.get("tool_name", "")
            tool_id = data.get("tool_id", "")
            params = data.get("parameters", {})

            return StreamEvent(
                type="tool_use",
                name=tool_name,
                tool_id=tool_id,
                input=params,
                status="running",
            )

        # ── tool_result ──
        if event_type == "tool_result":
            tool_id = data.get("tool_id", "")
            status = data.get("status", "")
            output = data.get("output", "")
            error = data.get("error", {})

            return StreamEvent(
                type="tool_result",
                tool_id=tool_id,
                output=output if isinstance(output, str) else json.dumps(output),
                status="failed" if status == "error" or error else "completed",
            )

        # ── result ──
        if event_type == "result":
            stats = data.get("stats", {})
            if stats:
                self._usage = {
                    "input_tokens": stats.get("input_tokens", stats.get("input", 0)),
                    "output_tokens": stats.get("output_tokens", 0),
                    "total_tokens": stats.get("total_tokens", 0),
                }
            return None

        # ── error ──
        if event_type == "error":
            msg = data.get("message", data.get("error", "Unknown error"))
            if isinstance(msg, dict):
                msg = msg.get("message", str(msg))
            return StreamEvent(type="error", content=str(msg))

        return None

    def _is_diagnostic(self, line: str) -> bool:
        """过滤 Gemini CLI 的诊断噪音"""
        return (
            line.startswith("Ripgrep")
            or line.startswith("Recording metric")
            or line.startswith("Loaded cached")
            or line.startswith("StartupProfiler")
            or line.startswith("Hook registry")
            or line.strip().startswith("at ")
        )

    def _extract_error(self, stderr: str) -> str:
        """从 stderr 提取友好错误信息"""
        lower = stderr.lower()
        if "quota_exhausted" in lower or "exhausted your capacity" in lower:
            return "Gemini API 配额已用尽，请稍后再试。"
        if "unauthorized" in lower or "authentication" in lower or "login" in lower:
            return "Gemini 未认证。请先运行 'gemini' 完成登录。"
        if "not found" in lower and "model" in lower:
            return "Gemini 模型不可用。请检查 --model 参数。"
        if "timeout" in lower or "timed out" in lower:
            return "Gemini 请求超时，请稍后再试。"
        if "network" in lower or "econnrefused" in lower or "enetunreach" in lower:
            return "网络连接失败，请检查网络。"
        return ""

    def _is_error_trace(self, line: str) -> bool:
        """检测是否是错误堆栈或崩溃信息"""
        markers = [
            "Error when talking to",
            "TerminalQuotaError",
            "QUOTA_EXHAUSTED",
            "Traceback (most recent call",
            "at classifyGoogleError",
            "at retryWithBackoff",
            "at process.processTicksAndRejections",
            "cause:",
            "retryDelayMs:",
            "reason:",
            "YOLO mode is enabled",
        ]
        return any(m in line for m in markers)


# ─── 命令行演示 ─────────────────────────────────────────────────────────────

def main():
    import sys

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "用 Python 写一个快速排序"

    print(f"[发送] {prompt}\n")
    print("=" * 60)

    gemini = GeminiTool()

    for event in gemini.send_sync(prompt):
        if event.type == "text":
            print(event.content, end="", flush=True)
        elif event.type == "tool_use":
            print(f"\n[工具] {event.name}({json.dumps(event.input, ensure_ascii=False)})")
        elif event.type == "session_active":
            print(f"\n[会话] session_id: {event.session_id}")
        elif event.type == "error":
            print(f"\n[错误] {event.content}", file=sys.stderr)
        elif event.type == "done":
            usage = event.usage
            if usage:
                print(f"\n[用量] input={usage.get('input_tokens', 0)} "
                      f"output={usage.get('output_tokens', 0)}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
