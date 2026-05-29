"""配置文件管理 — 支持全局和项目级配置"""

from __future__ import annotations
from pathlib import Path

# 默认配置
DEFAULTS = {
    "default_strategy": "quick",
    "default_agents": ["claude", "gemini"],
    "default_rounds": 2,
    "timeout": 60,
    "global_timeout": 300,
    "auto_converge": True,
    "output_format": "terminal",  # terminal | json | markdown
}

# 配置文件路径
GLOBAL_CONFIG = Path.home() / ".brainstorm" / "config.yaml"
LOCAL_CONFIG = Path(".brainstorm.yaml")


def _load_yaml(path: Path) -> dict:
    """加载 YAML 配置文件，不存在或解析失败返回空 dict"""
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 没有 pyyaml，尝试简单解析
        return _simple_parse_yaml(path)
    except Exception:
        return {}


def _simple_parse_yaml(path: Path) -> dict:
    """简单的 YAML 解析（不依赖 pyyaml，只支持扁平 key: value）"""
    result = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip()
                    # 类型推断
                    if value.lower() in ("true", "yes"):
                        result[key] = True
                    elif value.lower() in ("false", "no"):
                        result[key] = False
                    elif value.isdigit():
                        result[key] = int(value)
                    elif "," in value:
                        result[key] = [v.strip() for v in value.split(",")]
                    else:
                        result[key] = value.strip("\"'")
    except Exception:
        pass
    return result


def load_config() -> dict:
    """
    加载配置：全局配置 → 项目级配置 → 合并。
    项目级覆盖全局配置。
    """
    config = dict(DEFAULTS)

    # 全局配置
    global_cfg = _load_yaml(GLOBAL_CONFIG)
    config.update(global_cfg)

    # 项目级配置（覆盖全局）
    local_cfg = _load_yaml(LOCAL_CONFIG)
    config.update(local_cfg)

    # 规范化：确保 default_agents 是 list
    agents = config.get("default_agents", ["claude", "gemini"])
    if isinstance(agents, str):
        config["default_agents"] = [a.strip() for a in agents.split(",")]

    return config


def show_config(config: dict):
    """打印当前生效配置"""
    print(f"\n📋 当前配置")
    print(f"{'─' * 55}")

    sources = []
    if GLOBAL_CONFIG.exists():
        sources.append(f"  全局: {GLOBAL_CONFIG}")
    if LOCAL_CONFIG.exists():
        sources.append(f"  项目: {LOCAL_CONFIG.absolute()}")
    if not sources:
        sources.append("  (使用默认配置)")
    print("\n".join(sources))
    print(f"{'─' * 55}")

    for key, value in config.items():
        print(f"  {key}: {value}")
    print()


def create_default_config(path: Path | None = None):
    """创建默认配置文件"""
    target = path or GLOBAL_CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)

    content = """# Brainstorm 配置文件
# 全局配置: ~/.brainstorm/config.yaml
# 项目配置: .brainstorm.yaml（覆盖全局）

# 默认策略: quick | debate | red-team | perspectives | delphi
default_strategy: quick

# 默认 Agent（逗号分隔）
default_agents: claude, gemini

# 默认讨论轮数
default_rounds: 2

# 单个 Agent 超时（秒）
timeout: 60

# 全局会话超时（秒）
global_timeout: 300

# 自动收敛检测
auto_converge: true

# 输出格式: terminal | json | markdown
output_format: terminal
"""

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ 配置文件已创建: {target}")
