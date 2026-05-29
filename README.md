# Brainstorm

多 AI 头脑风暴 CLI 工具 — 让 Claude 和 Gemini 协作讨论，产出更全面的建议。

## 安装

```bash
# 前置条件：安装 Claude 和/或 Gemini CLI
npm install -g @anthropic-ai/claude-code
npm install -g @google/gemini-cli

# 安装 brainstorm
git clone https://github.com/yachenyanyi/brainstorm.git
cd brainstorm
pip install -e .
```

## 快速开始

```bash
# 检查环境
brainstorm --check

# 最简单的用法
brainstorm "什么是REST API？"

# 使用不同策略
brainstorm "应该用React还是Vue？" -s debate
brainstorm "审查这段代码" -s red-team
brainstorm "选型消息队列" -s delphi
```

## 5 种协作策略

| 策略 | 说明 | 适合场景 |
|------|------|----------|
| `quick` | 并行分析，直接综合 | 简单问题、快速获取多视角 |
| `debate` | 结构化辩论，互相批评 | 架构决策、技术选型 |
| `red-team` | 一个提案，一个找漏洞 | 安全审查、代码审查 |
| `perspectives` | 风险+创新双视角 | 平衡稳健和突破 |
| `delphi` | 主持人引导收敛到共识 | 复杂选型、多方权衡 |

## 常用选项

```bash
brainstorm "问题" -s debate -r 3      # 讨论3轮
brainstorm "问题" -q                   # 安静模式，只看结果
brainstorm "问题" --sequential         # 顺序输出，更清晰
brainstorm "问题" -o result.txt        # 输出到文件
brainstorm "问题" --json               # JSON格式输出
brainstorm "问题" --timeout 30         # 调整超时
brainstorm "问题" --timeout-gemini 90  # Gemini专用超时
```

## 新手引导

```bash
brainstorm --guide          # 策略选择指南
brainstorm --check          # 环境检测
brainstorm --show-config    # 查看配置
brainstorm --init-config    # 创建配置文件
```

## 配置文件

```bash
brainstorm --init-config
vim ~/.brainstorm/config.yaml
```

```yaml
default_strategy: debate
default_agents: claude, gemini
default_rounds: 3
timeout: 60
timeout_gemini: 120
global_timeout: 300
```

## 管道输入

```bash
echo "什么是REST API？" | brainstorm
cat question.txt | brainstorm -s debate
```

## 许可证

MIT License
