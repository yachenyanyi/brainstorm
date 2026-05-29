"""收敛检测算法 — 支持中英文混合文本"""

from __future__ import annotations
import re
from brainstorm.types import ConvergenceMetrics, DiscussionRound


# ─── 常量 ────────────────────────────────────────────────────────────────────

# 英文停用词
EN_STOPWORDS = frozenset([
    'a', 'an', 'the', 'is', 'it', 'in', 'on', 'at', 'to', 'of', 'or',
    'and', 'by', 'as', 'if', 'be', 'do', 'so', 'we', 'he', 'me', 'my', 'up', 'am',
])

# 中文停用词
ZH_STOPWORDS = frozenset([
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个',
    '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好',
    '自己', '这', '他', '她', '它', '们', '那', '些', '什么', '怎么', '这个', '那个',
])

# 英文关键词
EN_AGREE = [r'\bagree\b', r'\bconcede\b', r'\bvalid point\b',
            r'\bcorrect\b', r'\baccept\b', r'\bwell-taken\b']
EN_DISAGREE = [r'\bdisagree\b', r'\bhowever\b', r'\bincorrect\b',
               r'\bwrong\b', r'\breject\b', r'\bmaintain\b', r'\bdefend\b']

# 中文关键词
ZH_AGREE = [
    r'同意', r'赞同', r'认可', r'有道理', r'说得对', r'正确', r'合理',
    r'接受', r'确实', r'没错', r'好主意', r'可行', r'支持',
]
ZH_DISAGREE = [
    r'不同意', r'但是', r'然而', r'不过', r'错误', r'不对', r'有问题',
    r'反对', r'拒绝', r'坚持', r'仍然认为', r'并非', r'不能', r'不太',
    r'值得商榷', r'有待', r'质疑',
]

# 合并（中文不需要 \b 边界）
AGREE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EN_AGREE + ZH_AGREE]
DISAGREE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EN_DISAGREE + ZH_DISAGREE]

# Delphi 主持人评分正则（中英文都支持）
CONVERGENCE_SCORE_RE = re.compile(
    r'(?:convergence|consensus|agreement|收敛|共识|一致)\s*(?:score|level|rating|评分|分数)?:?\s*(\d+)\s*/\s*10',
    re.IGNORECASE,
)


# ─── 文本分词 ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """
    中英文混合分词。
    - 英文：按空格分词，去停用词，小写
    - 中文：按字符提取，去停用词
    - 返回统一的 token 集合
    """
    tokens = set()
    # 提取英文单词
    en_words = re.findall(r'[a-zA-Z]{2,}', text.lower())
    for w in en_words:
        if w not in EN_STOPWORDS:
            tokens.add(w)

    # 提取中文字符（去停用词）
    zh_chars = re.findall(r'[一-鿿]', text)
    for ch in zh_chars:
        if ch not in ZH_STOPWORDS:
            tokens.add(ch)

    # 提取中文双字词（简单 bigram，覆盖常用词）
    for i in range(len(zh_chars) - 1):
        bigram = zh_chars[i] + zh_chars[i + 1]
        if zh_chars[i] not in ZH_STOPWORDS and zh_chars[i + 1] not in ZH_STOPWORDS:
            tokens.add(bigram)

    return tokens


# ─── 核心算法 ────────────────────────────────────────────────────────────────

def calculate_text_similarity(text_a: str, text_b: str) -> float:
    """
    计算两段文本的相似度。
    Jaccard 变体：用 max(|A|,|B|) 做分母（而非 union），衡量包含关系。
    支持中英文混合文本。

    对标 Mysti _calculateTextSimilarity()
    """
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = len(tokens_a & tokens_b)
    return intersection / max(len(tokens_a), len(tokens_b))


def _count_patterns(text: str, patterns: list[re.Pattern]) -> int:
    """统计所有模式在文本中的出现次数"""
    count = 0
    for pattern in patterns:
        matches = pattern.findall(text)
        count += len(matches)
    return count


def assess_convergence(
    discussion_rounds: list[DiscussionRound],
    convergence_history: list[ConvergenceMetrics],
    facilitator_text: str | None = None,
) -> ConvergenceMetrics:
    """
    评估讨论轮次的收敛程度。

    对标 Mysti _assessConvergence()

    算法：
    1. 统计中英文同意/反对关键词出现次数
    2. 计算每个 Agent 与上一轮的文本相似度（位置稳定性）
    3. 加权计算综合收敛度 = 同意比 * 0.6 + 稳定性 * 0.4
    4. 判断：converged / stalled / continue

    Args:
        discussion_rounds: 所有讨论轮次
        convergence_history: 历史收敛记录
        facilitator_text: Delphi 主持人文本（可选，用于提取显式评分）
    """
    if not discussion_rounds:
        return ConvergenceMetrics(
            round=0, agreement_count=0, disagreement_count=0,
            agreement_ratio=0.5, position_stability={},
            overall_convergence=0.5, recommendation="continue",
        )

    last_round = discussion_rounds[-1]
    round_num = last_round.round_number

    agreement_count = 0
    disagreement_count = 0
    position_stability: dict[str, float] = {}
    has_empty_contribution = False

    for agent_id, contribution in last_round.contributions.items():
        if not contribution.strip():
            has_empty_contribution = True
            continue

        # 统计同意/反对信号（中英文）
        agreement_count += _count_patterns(contribution, AGREE_PATTERNS)
        disagreement_count += _count_patterns(contribution, DISAGREE_PATTERNS)

        # 位置稳定性：和上一轮对比
        if len(discussion_rounds) >= 2:
            prev_round = discussion_rounds[-2]
            prev_contribution = prev_round.contributions.get(agent_id, "")
            if prev_contribution.strip():
                similarity = calculate_text_similarity(prev_contribution, contribution)
                position_stability[agent_id] = similarity

    # 计算同意比
    total = agreement_count + disagreement_count
    if has_empty_contribution:
        agreement_ratio = 0.5
    elif total > 0:
        agreement_ratio = agreement_count / total
    else:
        agreement_ratio = 0.5

    # 平均稳定性
    stability_values = list(position_stability.values())
    avg_stability = (
        sum(stability_values) / len(stability_values)
        if stability_values else 0.5
    )

    # 综合收敛度
    overall_convergence = (agreement_ratio * 0.6) + (avg_stability * 0.4)

    # ── 判断 ──
    recommendation: str = "continue"

    # Delphi 特有：从主持人汇总中提取显式评分
    if facilitator_text:
        score_match = CONVERGENCE_SCORE_RE.search(facilitator_text)
        if score_match:
            score = int(score_match.group(1)) / 10.0
            if score >= 0.7:
                overall_convergence = max(overall_convergence, score)
                recommendation = "converged"

    if recommendation != "converged":
        # 收敛条件
        if not has_empty_contribution and agreement_ratio >= 0.7 and avg_stability >= 0.8:
            recommendation = "converged"
        # 僵局检测
        elif len(convergence_history) >= 2:
            prev = convergence_history[-1]

            # 原始僵局：没有进步且稳定性低
            if prev.overall_convergence >= overall_convergence and avg_stability < 0.3:
                recommendation = "stalled"

            # 振荡检测：第 N 轮和第 N-2 轮相似（>=0.7）
            if recommendation == "continue" and len(discussion_rounds) >= 3:
                two_rounds_ago = discussion_rounds[-3]
                oscillating = True
                for agent_id, contribution in last_round.contributions.items():
                    old_contribution = two_rounds_ago.contributions.get(agent_id, "")
                    if old_contribution and contribution.strip() and old_contribution.strip():
                        sim = calculate_text_similarity(old_contribution, contribution)
                        if sim < 0.7:
                            oscillating = False
                            break
                    else:
                        oscillating = False
                        break
                if oscillating:
                    recommendation = "stalled"

    return ConvergenceMetrics(
        round=round_num,
        agreement_count=agreement_count,
        disagreement_count=disagreement_count,
        agreement_ratio=agreement_ratio,
        position_stability=position_stability,
        overall_convergence=overall_convergence,
        recommendation=recommendation,  # type: ignore
    )
