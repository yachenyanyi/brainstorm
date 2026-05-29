"""
Claude Code CLI Python 封装 - 可作为工具给其他 Agent 调用

支持两种模式:
  1. 常驻进程模式 (persistent) - 一个进程服务多轮对话，默认使用
  2. 单次模式 (single-shot) - 每轮新建进程，作为兜底

用法:
  from claude_tool import ClaudeTool

  # 异步用法（推荐）
  async with ClaudeTool() as claude:
      async for event in claude.send("帮我写一个排序算法"):
          if event.type == "text":
              print(event.content, end="", flush=True)

  # 同步用法
  claude = ClaudeTool()
  for event in claude.send_sync("hello"):
      print(event)
  claude.close()
"""

import json
import asyncio
import signal
import os
from dataclasses import dataclass, field
from typing import AsyncGenerator, Generator


# ─── 数据类型 ───────────────────────────────────────────────────────────────

@dataclass
class StreamEvent:
    """统一的流事件"""
    type: str                                        # text/thinking/tool_use/tool_result/session_active/error/done
    content: str = ""                                # text / thinking / error 的内容
    name: str = ""                                   # tool_use 的工具名
    tool_id: str = ""                                # tool_use / tool_result 的 ID
    input: dict = field(default_factory=dict)        # tool_use 的完整参数
    output: str = ""                                 # tool_result 的输出
    status: str = ""                                 # tool_use: running; tool_result: completed/failed
    session_id: str = ""                             # session_active 时返回
    usage: dict = field(default_factory=dict)        # done 时的 token 用量


# ─── 主类 ────────────────────────────────────────────────────────────────────

class ClaudeTool:
    """
    Claude Code CLI 的 Python 封装。
    对标 Mysti 的 ClaudeCodeProvider + BaseCliProvider 实现。
    使用 asyncio.create_subprocess_exec 实现真正的异步 IO。
    """

    def __init__(
        self,
        cli_path: str = "claude",
        model: str | None = None,
        permission_mode: str = "bypass",              # bypass / plan
        system_prompt: str | None = None,
        session_id: str | None = None,
        use_persistent: bool = True,
    ):
        self.cli_path = cli_path
        self.model = model
        self.permission_mode = permission_mode
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.use_persistent = use_persistent

        # 内部状态
        self._proc: asyncio.subprocess.Process | None = None
        self._persistent_ready = False
        self._usage: dict = {}
        # 工具输入累积 (index → {id, name, input_json})
        self._active_tools: dict[int, dict] = {}
        # 去重：标记是否已收到 stream_event 增量
        self._has_streamed_text = False
        self._has_streamed_thinking = False

    # ─── 上下文管理器 ──────────────────────────────────────────────────────

    async def __aenter__(self):
        if self.use_persistent:
            await self._spawn_persistent()
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    # ─── 公开 API ──────────────────────────────────────────────────────────

    async def send(self, prompt: str) -> AsyncGenerator[StreamEvent, None]:
        """
        发送消息，异步生成器产出 StreamEvent。
        优先使用常驻进程，失败则回退到单次模式。
        """
        if self.use_persistent and self._persistent_ready:
            try:
                had_events = False
                async for event in self._send_persistent(prompt):
                    had_events = True
                    yield event
                if had_events:
                    return
            except Exception:
                await self.close()

        # 回退到单次模式
        async for event in self._send_single_shot(prompt):
            yield event

    def send_sync(self, prompt: str) -> Generator[StreamEvent, None, None]:
        """同步版本，适合非 async 环境"""
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

    # 对标 BaseCliProvider.disposePersistentProcess()
    # SIGTERM → 等待 grace period (5s) → SIGKILL
    _KILL_GRACE_SECONDS = 5

    async def aclose(self):
        """异步关闭持久化进程：SIGTERM → 等待 → SIGKILL"""
        proc = self._proc
        if not proc:
            return
        self._proc = None
        self._persistent_ready = False
        try:
            proc.terminate()  # SIGTERM
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()  # SIGKILL
                await proc.wait()
        except ProcessLookupError:
            pass  # 进程已退出
        except Exception:
            pass

    def close(self):
        """
        同步关闭：SIGTERM，不等待。
        对标 BaseCliProvider.dispose() — 只发 SIGTERM，SIGKILL 由 OS 在进程退出时清理。
        如果需要确保进程被杀，用 aclose()。
        """
        proc = self._proc
        if not proc:
            return
        self._proc = None
        self._persistent_ready = False
        try:
            pid = proc.pid
            if pid:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # 进程已退出
        except Exception:
            pass

    @property
    def last_usage(self) -> dict:
        return self._usage

    @property
    def current_session_id(self) -> str | None:
        return self.session_id

    # ─── 常驻进程模式 ──────────────────────────────────────────────────────

    async def _spawn_persistent(self):
        """启动常驻进程"""
        args = self._build_persistent_args()
        self._proc = await asyncio.create_subprocess_exec(
            self.cli_path, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._persistent_ready = True

    def _build_persistent_args(self) -> list[str]:
        args = [
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        args.extend(self._permission_flags())
        if self.session_id:
            args.extend(["--resume", self.session_id])
        if self.model:
            args.extend(["--model", self.model])
        if self.system_prompt:
            args.extend(["--append-system-prompt", self.system_prompt])
        return args

    async def _send_persistent(self, prompt: str) -> AsyncGenerator[StreamEvent, None]:
        """通过常驻进程发送消息"""
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("Persistent process not running")

        # 构造 JSON 输入 (对标 _formatPersistentInput)
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        }
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

        # 逐行读取，直到遇到 result 事件
        async for line in self._readlines(self._proc.stdout):
            event = self._parse_line(line)
            if event:
                yield event
            # result 事件标志着一轮响应结束
            try:
                data = json.loads(line)
                if data.get("type") == "result":
                    return
            except (json.JSONDecodeError, ValueError):
                pass

    # ─── 单次模式 ──────────────────────────────────────────────────────────

    async def _send_single_shot(self, prompt: str) -> AsyncGenerator[StreamEvent, None]:
        """
        单次模式：spawn → 写 prompt → 读流 → 杀进程。
        对标 BaseCliProvider._sendSingleShot() + processStream()
        """
        args = self._build_single_shot_args()

        proc = await asyncio.create_subprocess_exec(
            self.cli_path, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 写入 prompt 后关闭 stdin
        proc.stdin.write(prompt.encode())
        proc.stdin.close()

        try:
            # 逐行读取 stdout
            async for line in self._readlines(proc.stdout):
                event = self._parse_line(line)
                if event:
                    yield event

            # 等待进程退出
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()

            # 检查错误
            if proc.returncode and proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode(errors="replace")
                friendly = self._extract_error(stderr)
                if friendly:
                    yield StreamEvent(type="error", content=friendly)
                elif stderr.strip():
                    yield StreamEvent(type="error", content=stderr.strip()[:200])

        finally:
            try:
                proc.kill()
            except Exception:
                pass

        yield StreamEvent(type="done", usage=self._usage)

    def _build_single_shot_args(self) -> list[str]:
        args = [
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--print",                           # 单次模式的关键标志
        ]
        args.extend(self._permission_flags())
        if self.session_id:
            args.extend(["--resume", self.session_id])
        if self.model:
            args.extend(["--model", self.model])
        if self.system_prompt:
            args.extend(["--append-system-prompt", self.system_prompt])
        return args

    # ─── 异步逐行读取 ──────────────────────────────────────────────────────

    async def _readlines(self, stream: asyncio.streams.StreamReader) -> AsyncGenerator[str, None]:
        """从 asyncio StreamReader 逐行读取 JSON"""
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                yield text

    # ─── 权限标志 ──────────────────────────────────────────────────────────

    def _permission_flags(self) -> list[str]:
        """
        权限标志映射。
        对标 ClaudeCodeProvider._addPermissionFlags()
        """
        if self.permission_mode == "plan":
            return ["--permission-mode", "plan"]
        return ["--dangerously-skip-permissions"]

    # ─── 流解析 ────────────────────────────────────────────────────────────

    def _extract_error(self, stderr: str) -> str:
        """从 stderr 提取友好错误信息"""
        lower = stderr.lower()
        if "unauthorized" in lower or "authentication" in lower or "login" in lower:
            return "Claude 未认证。请先运行 'claude' 完成登录。"
        if "timeout" in lower or "timed out" in lower:
            return "Claude 请求超时，请稍后再试。"
        if "not found" in lower and ("model" in lower or "command" in lower):
            return "Claude CLI 或模型不可用。"
        if "network" in lower or "econnrefused" in lower:
            return "网络连接失败，请检查网络。"
        if "rate" in lower and "limit" in lower:
            return "Claude API 请求频率超限，请稍后再试。"
        return ""

    def _parse_line(self, line: str) -> StreamEvent | None:
        """
        解析 Claude CLI 输出的单行 JSON。
        对标 ClaudeCodeProvider.parseStreamLine() (line 392-639)
        """
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            if line.strip():
                return StreamEvent(type="text", content=line)
            return None

        if data.get("type") == "stream_event":
            return self._parse_stream_event(data)

        if data.get("type") == "system":
            return self._parse_system_event(data)

        # ── assistant 完整消息（非 stream_event 模式时出现）──
        if data.get("type") == "assistant":
            return self._parse_assistant_message(data)

        if data.get("type") == "result":
            # 从 result 中提取用量（备用来源）
            usage = data.get("usage", {})
            if usage and not self._usage:
                self._usage = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                }
            return None

        if data.get("type") == "error":
            msg = data.get("error", {}).get("message") or data.get("message", "Unknown error")
            return StreamEvent(type="error", content=msg)

        if data.get("type") == "tool_result":
            content = data.get("content", "")
            return StreamEvent(
                type="tool_result",
                tool_id=data.get("tool_use_id", data.get("tool_id", "")),
                output=content if isinstance(content, str) else json.dumps(content),
                status="failed" if data.get("is_error") else "completed",
            )

        return None

    def _parse_stream_event(self, data: dict) -> StreamEvent | None:
        """解析 stream_event 内嵌事件，带工具输入累积"""
        event = data.get("event", {})
        etype = event.get("type", "")
        index = event.get("index", -1)

        # content_block_delta
        if etype == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "text_delta":
                self._has_streamed_text = True
                return StreamEvent(type="text", content=delta.get("text", ""))

            if delta_type == "thinking_delta":
                self._has_streamed_thinking = True
                return StreamEvent(type="thinking", content=delta.get("thinking", ""))

            if delta_type == "input_json_delta":
                # 累积工具输入 JSON
                if index in self._active_tools:
                    self._active_tools[index]["input_json"] += delta.get("partial_json", "")
                return None

        # content_block_start
        if etype == "content_block_start":
            block = event.get("content_block", {})
            block_type = block.get("type", "")

            if block_type == "tool_use":
                self._active_tools[index] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input_json": "",
                }
                return StreamEvent(
                    type="tool_use",
                    name=block.get("name", ""),
                    tool_id=block.get("id", ""),
                    input={},
                    status="running",
                )

            if block_type == "thinking":
                return StreamEvent(type="thinking", content="")

        # content_block_stop - 工具输入累积完成
        if etype == "content_block_stop":
            if index in self._active_tools:
                tool = self._active_tools.pop(index)
                parsed_input = {}
                if tool["input_json"]:
                    try:
                        parsed_input = json.loads(tool["input_json"])
                    except json.JSONDecodeError:
                        parsed_input = {"_raw": tool["input_json"]}
                return StreamEvent(
                    type="tool_use",
                    name=tool["name"],
                    tool_id=tool["id"],
                    input=parsed_input,
                    status="running",
                )

        # message_delta - 捕获 token 用量
        if etype == "message_delta":
            usage = event.get("usage", {})
            if usage:
                self._usage = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                }
            return None

        if etype == "message_start":
            self._has_streamed_text = False
            self._has_streamed_thinking = False
            return None

        if etype == "message_stop":
            return None

        return None

    def _parse_assistant_message(self, data: dict) -> StreamEvent | None:
        """
        解析 assistant 完整消息。
        CLI 同时输出 stream_event（增量）和 assistant（完整），需要去重：
        如果已经收到 stream_event 增量文本，跳过 assistant 完整消息。
        """
        message = data.get("message", {})
        content_blocks = message.get("content", [])

        # 捕获用量（即使跳过也要更新）
        usage = message.get("usage", {})
        if usage and usage.get("output_tokens", 0) > 0:
            self._usage = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            }

        # 如果已经通过 stream_event 收到过增量内容，跳过 assistant 完整消息
        if self._has_streamed_text or self._has_streamed_thinking:
            return None

        # 回退：没有 stream_event 时，从 assistant 消息中提取内容
        for block in content_blocks:
            btype = block.get("type", "")

            if btype == "text":
                return StreamEvent(type="text", content=block.get("text", ""))

            if btype == "thinking":
                return StreamEvent(type="thinking", content=block.get("thinking", ""))

            if btype == "tool_use":
                return StreamEvent(
                    type="tool_use",
                    name=block.get("name", ""),
                    tool_id=block.get("id", ""),
                    input=block.get("input", {}),
                    status="running",
                )

        return None

    def _parse_system_event(self, data: dict) -> StreamEvent | None:
        """解析 system 事件"""
        if data.get("subtype") == "init":
            sid = data.get("session_id") or data.get("sessionId")
            if sid and not self.session_id:
                self.session_id = sid
                return StreamEvent(type="session_active", session_id=sid)
        return None


# ─── 命令行演示 ─────────────────────────────────────────────────────────────

def main():
    import sys

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "用 Python 写一个快速排序"

    print(f"[发送] {prompt}\n")
    print("=" * 60)

    claude = ClaudeTool(
        use_persistent=False,
    )

    for event in claude.send_sync(prompt):
        if event.type == "text":
            print(event.content, end="", flush=True)
        elif event.type == "thinking" and event.content:
            print(f"\n[思考] {event.content}", end="", flush=True)
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
