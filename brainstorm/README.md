# Brainstorm

多 AI 头脑风暴 CLI 工具 — 让 Claude 和 Gemini 协作讨论，产出更全面的建议。

## 安装

```bash
# 从源码安装
git clone <repo-url>
cd brainstorm
pip install -e .

# 或直接安装
pip install .
```

安装后直接使用 `brainstorm` 命令：

```bash
brainstorm "你的问题"
```

## 前置条件

需要安装以下 CLI 工具（至少一个）：

- **Claude CLI**: `npm install -g @anthropic-ai/claude-code`
- **Gemini CLI**: `npm install -g @google/gemini-cli`

检查环境：

```bash
brainstorm --check
```

## 快速开始

```bash
# 最简单的用法
brainstorm "什么是REST API？"

# 使用策略
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
# 调整讨论轮数
brainstorm "设计认证方案" -s debate -r 3

# 安静模式（只看结果）
brainstorm "什么是API？" -q

# 顺序输出（更清晰）
brainstorm "什么是API？" --sequential

# 输出到文件
brainstorm "设计数据库方案" -o result.txt

# JSON 输出
brainstorm "什么是API？" --json

# 调整超时
brainstorm "什么是API？" --timeout 30
brainstorm "什么是API？" --timeout-claude 30 --timeout-gemini 90
```

## 新手引导

```bash
# 策略选择指南
brainstorm --guide

# 环境检测
brainstorm --check

# 查看配置
brainstorm --show-config

# 创建配置文件
brainstorm --init-config
```

## 配置文件

```bash
# 创建默认配置
brainstorm --init-config

# 编辑配置
vim ~/.brainstorm/config.yaml
```

配置示例：

```yaml
# 默认策略
default_strategy: debate

# 默认 Agent
default_agents: claude, gemini

# 默认讨论轮数
default_rounds: 3

# 超时设置（秒）
timeout: 60
timeout_claude: 60
timeout_gemini: 120
global_timeout: 300
```

## 管道输入

```bash
echo "什么是REST API？" | brainstorm
cat question.txt | brainstorm -s debate
```

## 开发

```bash
# 开发模式安装
pip install -e .

# 运行测试
python -m pytest tests/

# 打包
python -m build
```

## 许可证

MIT License
