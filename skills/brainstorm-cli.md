---
name: brainstorm-cli
description: "Multi-AI brainstorm CLI — let Claude and Gemini collaborate to produce better answers via structured strategies (quick/debate/red-team/perspectives/delphi)."
version: 1.0.0
author: User
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [brainstorm, ai, multi-agent, cli, claude, gemini, decision-making]
    related_skills: [claude-code]
---

# Brainstorm CLI

Multi-AI brainstorm tool that makes Claude and Gemini collaborate on your question, producing more comprehensive answers than either alone.

## When to use

- User asks to "brainstorm", "discuss", "compare", "debate", "review" a topic
- User wants multiple AI perspectives on a question
- User needs structured analysis (risk/opportunity, red team, consensus)
- User asks "should I use X or Y?" type questions

## Prerequisites

Check environment first:

```bash
brainstorm --check
```

Requires Claude CLI and/or Gemini CLI installed:
- `npm install -g @anthropic-ai/claude-code`
- `npm install -g @google/gemini-cli`

## 5 Strategies

| Strategy | Flag | When to use |
|----------|------|-------------|
| **quick** | `-s quick` (default) | Simple questions, fast multi-perspective |
| **debate** | `-s debate` | Architecture decisions, tech choices |
| **red-team** | `-s red-team` | Security review, code audit, stress-testing proposals |
| **perspectives** | `-s perspectives` | Balancing risk vs innovation |
| **delphi** | `-s delphi` | Complex multi-factor decisions, consensus-building |

## Strategy recommendation

Pick strategy based on the question type:

- "审查/检查/review/audit" → `red-team`
- "选型/对比/应该用/which/compare" → `delphi`
- "设计/架构/design/architecture" → `debate`
- "风险/risk/权衡/tradeoff" → `perspectives`
- Everything else → `quick`

## Common commands

```bash
# Basic usage
brainstorm "your question"

# With strategy
brainstorm "should we use React or Vue?" -s debate

# More rounds = deeper discussion
brainstorm "design auth system" -s debate -r 3

# Quiet mode (synthesis only)
brainstorm "what is REST?" -q

# Sequential output (cleaner in terminal)
brainstorm "what is REST?" --sequential

# Save to file
brainstorm "design database schema" -o result.txt

# JSON output (for parsing)
brainstorm "what is API?" --json

# Adjust timeout (Gemini is usually slower)
brainstorm "question" --timeout-gemini 120

# Pipe input
echo "explain GIL" | brainstorm
cat question.txt | brainstorm -s debate
```

## Key options

| Option | Default | Description |
|--------|---------|-------------|
| `-s` | quick | Strategy |
| `-a` | claude,gemini | Agents (comma-separated) |
| `-r` | 2 | Max discussion rounds |
| `-q` | off | Quiet mode (synthesis only) |
| `--sequential` | off | Non-interleaved output |
| `-o` | stdout | Output to file |
| `--json` | off | JSON output |
| `--timeout` | 60 | Per-agent timeout (seconds) |
| `--timeout-gemini` | — | Gemini-specific timeout |
| `--no-converge` | off | Force full rounds |

## Output phases

1. **Individual** — Both agents analyze in parallel
2. **Discussion** — Agents critique/refine each other (debate/red-team/perspectives/delphi)
3. **Synthesis** — Final combined recommendation

## Pitfalls

- **Gemini is slower** than Claude. Use `--timeout-gemini 120` if Gemini times out.
- **Gemini quota** — Free tier has limits. If "配额已用尽" error, wait or use Claude only: `-a claude,claude` (but need 2 different agents, so just wait).
- **Long questions** — Keep queries concise. Context goes in the question itself.
- **`-q` mode** still shows the synthesis phase header. Use `--json` for truly clean output.
- **`--sequential`** buffers each agent's output before displaying. Better for reading, but no streaming feel.

## Integration with Hermes

Use brainstorm when:
- User asks a complex question that benefits from multiple perspectives
- User says "brainstorm this" or "discuss this"
- User needs a structured analysis (not just a quick answer)

Example flow:
```
User: "Should I migrate to microservices?"
→ brainstorm "Should I migrate to microservices?" -s debate -r 3 -q
→ Show the synthesis result to the user
```

## Installation

```bash
pip install -e /path/to/brainstorm
# or
pip install git+https://github.com/yachenyanyi/brainstorm.git
```
