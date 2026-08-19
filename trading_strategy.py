from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["long", "short"]


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class Bar:
    """OHLCV bar with date string. Used by A-share and US stock strategy functions."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TradePlan:
    side: Side
    entry: float
    stop: float
    target: float
    support: float
    resistance: float
    risk_reward: float
    confidence: int
    decision: str
    setup: str
    reasons: list[str]
    add_plan: str


@dataclass(frozen=True)
class FixedStopRR:
    """固定 5% 止损规则下的风险/收益计算结果（2026-06-04 裸 K 规则）。"""

    current_price: float
    nearby_support: float | None
    next_pressure: float | None
    stop_price: float
    risk: float
    reward: float
    reward_to_risk: float
    is_valid_long_candidate: bool
    invalid_reason: str | None = None


@dataclass(frozen=True)
class SetupQuality:
    """裸 K setup 质量评分（用于 rank_best_long_candidates 综合排序）。"""

    setup_label: str
    score: float  # 0-100，越高越好
    is_actionable: bool  # 是否可作为严格做多候选


# Setup 质量映射（与 a_share_daily_agent.score_price_action 中的 setup 字符串一致）
SETUP_QUALITY_TABLE: dict[str, SetupQuality] = {
    "回踩50%": SetupQuality("回踩50%", 94.0, True),
    "突破后回踩": SetupQuality("突破后回踩", 88.0, True),
    "二波回踩EMA20": SetupQuality("二波回踩EMA20", 92.0, True),
    "突破EMA20": SetupQuality("突破EMA20", 85.0, True),
    "下降通道突破": SetupQuality("下降通道突破", 80.0, True),
}


def identify_nearby_support(
    candles: list[Candle],
    current_price: float,
    lookback: int = 20,
    max_distance_pct: float = 0.05,
) -> float | None:
    """识别当前价格下方最近的支撑位。

    取最近 ``lookback`` 根 K 线中、位于 ``current_price`` 下方且距离不超过
    ``max_distance_pct`` 比例的最低 ``low``。若没有满足条件的支撑位，返回 ``None``。

    缺失/无效输入（空列表、价格 <= 0、lookback <= 0）一律返回 ``None``，
    不会抛异常，方便上层做"缺失则过滤"的逻辑。
    """
    if not candles or current_price <= 0 or lookback <= 0:
        return None
    window = candles[-lookback:] if len(candles) >= lookback else candles
    floor = current_price * (1.0 - max_distance_pct)
    candidates = [candle.low for candle in window if candle.low > 0 and candle.low <= current_price and candle.low >= floor]
    if not candidates:
        return None
    # 取最接近当前价的支撑（即最高的那一个）
    return max(candidates)


def identify_next_pressure(
    candles: list[Candle],
    current_price: float,
    lookback: int = 120,
    min_pct_above: float = 0.025,
) -> float | None:
    """识别当前价格上方的下一压力位。

    取最近 ``lookback`` 根 K 线中、位于 ``current_price * (1 + min_pct_above)``
    之上的最低 ``high``。若没有满足条件的压力位，返回 ``None``。

    与 ``identify_nearby_support`` 一致：缺失/无效输入返回 ``None``，不抛异常。
    """
    if not candles or current_price <= 0 or lookback <= 0:
        return None
    window = candles[-lookback:] if len(candles) >= lookback else candles
    ceiling = current_price * (1.0 + min_pct_above)
    candidates = [candle.high for candle in window if candle.high > ceiling]
    if not candidates:
        return None
    # 取最接近当前价的压力（即最低的那一个）
    return min(candidates)


def compute_fixed_stop_rr(
    current_price: float,
    nearby_support: float | None,
    next_pressure: float | None,
    stop_pct: float = 0.05,
) -> FixedStopRR:
    """按 2026-06-04 裸 K 规则计算固定百分比止损的风险/收益。

    - ``stop_price = current_price * (1 - stop_pct)``
    - ``risk = current_price - stop_price``
    - ``reward = next_pressure - current_price``
    - ``reward_to_risk = reward / risk``

    返回值 ``is_valid_long_candidate`` 为 ``False`` 的场景（任一即视为无效）：
    1. ``current_price <= 0`` 或 ``stop_pct <= 0`` 或 ``stop_pct >= 1``；
    2. ``next_pressure`` 缺失；
    3. ``reward <= 0``（next_pressure 已经在 current_price 下方）；
    4. ``risk <= 0``（current_price 不大于 stop_price）。

    ``invalid_reason`` 字段记录失效原因，便于上层在 Markdown/Lark 中展示。
    """
    if current_price <= 0 or stop_pct <= 0 or stop_pct >= 1:
        return FixedStopRR(
            current_price=current_price,
            nearby_support=nearby_support,
            next_pressure=next_pressure,
            stop_price=0.0,
            risk=0.0,
            reward=0.0,
            reward_to_risk=0.0,
            is_valid_long_candidate=False,
            invalid_reason="invalid_input",
        )

    stop_price = current_price * (1.0 - stop_pct)
    risk = current_price - stop_price
    if risk <= 0:
        return FixedStopRR(
            current_price=current_price,
            nearby_support=nearby_support,
            next_pressure=next_pressure,
            stop_price=stop_price,
            risk=risk,
            reward=0.0,
            reward_to_risk=0.0,
            is_valid_long_candidate=False,
            invalid_reason="risk_non_positive",
        )

    if next_pressure is None:
        return FixedStopRR(
            current_price=current_price,
            nearby_support=nearby_support,
            next_pressure=None,
            stop_price=stop_price,
            risk=risk,
            reward=0.0,
            reward_to_risk=0.0,
            is_valid_long_candidate=False,
            invalid_reason="missing_next_pressure",
        )

    reward = next_pressure - current_price
    if reward <= 0:
        return FixedStopRR(
            current_price=current_price,
            nearby_support=nearby_support,
            next_pressure=next_pressure,
            stop_price=stop_price,
            risk=risk,
            reward=reward,
            reward_to_risk=0.0,
            is_valid_long_candidate=False,
            invalid_reason="reward_non_positive",
        )

    return FixedStopRR(
        current_price=current_price,
        nearby_support=nearby_support,
        next_pressure=next_pressure,
        stop_price=stop_price,
        risk=risk,
        reward=reward,
        reward_to_risk=reward / risk,
        is_valid_long_candidate=True,
    )


def rank_best_long_candidates(
    candidates: list[dict],
    rr_key: str = "reward_to_risk",
    setup_key: str = "setup",
    final_score_key: str = "final_score",
    bullish_confidence_key: str = "bullish_confidence",
) -> list[dict]:
    """按 reward_to_risk + setup 质量 + 现有 final_score 综合排序做多候选。

    仅返回 ``is_valid_long_candidate is True`` 的候选；其余一律过滤。
    排序规则：
      1. ``reward_to_risk`` 降序（主键）
      2. setup 质量分数降序（次键，使用 ``SETUP_QUALITY_TABLE``）
      3. ``final_score`` 降序
      4. ``bullish_confidence`` 降序
    输入候选为 ``dict``（每个 dict 至少包含上述 4 个键），便于直接接 ``AShareCandidate`` 的 ``__dict__``。
    """
    valid: list[dict] = []
    for candidate in candidates:
        if "is_valid_long_candidate" in candidate and candidate.get("is_valid_long_candidate") is not True:
            continue
        rr = candidate.get(rr_key)
        if rr is None or rr <= 0:
            continue
        setup_quality = SETUP_QUALITY_TABLE.get(candidate.get(setup_key, ""))
        if setup_quality is None or not setup_quality.is_actionable:
            continue
        valid.append(candidate)

    def sort_key(item: dict):
        setup_quality = SETUP_QUALITY_TABLE.get(item.get(setup_key, ""), SetupQuality(item.get(setup_key, ""), 0.0, False))
        return (
            -float(item.get(rr_key, 0.0)),
            -setup_quality.score,
            -float(item.get(final_score_key, 0.0)),
            -float(item.get(bullish_confidence_key, 0.0)),
        )

    return sorted(valid, key=sort_key)


def format_rr_markdown_row(rr: FixedStopRR) -> str:
    """把 ``FixedStopRR`` 渲染成单行 Markdown 字段片段（用于日报表格）。"""
    if not rr.is_valid_long_candidate:
        reason = rr.invalid_reason or "invalid"
        return f"5%止损: n/a({reason}); RR: n/a"
    support_text = f"{rr.nearby_support:.2f}" if rr.nearby_support is not None else "n/a"
    return (
        f"5%止损 {rr.stop_price:.2f} (风险 {rr.risk:.2f}); "
        f"下一压力 {rr.next_pressure:.2f} (空间 {rr.reward:.2f}); "
        f"RR {rr.reward_to_risk:.2f}; 支撑 {support_text}"
    )


def format_rr_lark_line(rr: FixedStopRR) -> str:
    """把 ``FixedStopRR`` 渲染成单行 Lark/飞书消息片段。"""
    if not rr.is_valid_long_candidate:
        return f"[RR无效:{rr.invalid_reason or 'invalid'}]"
    support_text = f"{rr.nearby_support:.2f}" if rr.nearby_support is not None else "n/a"
    return (
        f"5%止损={rr.stop_price:.2f} 风险={rr.risk:.2f} "
        f"下一压力={rr.next_pressure:.2f} 空间={rr.reward:.2f} "
        f"RR={rr.reward_to_risk:.2f} 支撑={support_text}"
    )


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def macd_series(closes: list[float]) -> list[float]:
    values: list[float] = []
    for index in range(len(closes)):
        window = closes[: index + 1]
        ema_12 = ema(window, 12)
        ema_26 = ema(window, 26)
        if ema_12 is not None and ema_26 is not None:
            values.append(ema_12 - ema_26)
        else:
            values.append(0.0)
    return values


def support_resistance(candles: list[Candle], lookback: int = 40) -> tuple[float, float]:
    window = candles[-lookback:] if len(candles) >= lookback else candles
    if not window:
        return 0.0, 0.0
    support = min(candle.low for candle in window)
    resistance = max(candle.high for candle in window)
    return support, resistance


def bullish_engulfing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    return (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
    )


def bearish_engulfing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    return (
        previous.close > previous.open
        and current.close < current.open
        and current.open >= previous.close
        and current.close <= previous.open
    )


def morning_star(candles: list[Candle]) -> bool:
    if len(candles) < 3:
        return False
    first, second, third = candles[-3], candles[-2], candles[-1]
    first_body = abs(first.close - first.open)
    second_body = abs(second.close - second.open)
    midpoint = (first.open + first.close) / 2
    return first.close < first.open and second_body < first_body * 0.45 and third.close > midpoint


def bearish_piercing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    midpoint = (previous.open + previous.close) / 2
    return previous.close > previous.open and current.close < current.open and current.close < midpoint


def double_top(candles: list[Candle], tolerance: float = 0.015) -> bool:
    if len(candles) < 12:
        return False
    highs = [candle.high for candle in candles[-40:]]
    top = max(highs)
    near_tops = [high for high in highs if abs(high - top) / top <= tolerance]
    return len(near_tops) >= 2 and candles[-1].close < top * (1 - tolerance)


def multiple_top(candles: list[Candle], tolerance: float = 0.02) -> bool:
    if len(candles) < 20:
        return False
    highs = [candle.high for candle in candles[-60:]]
    top = max(highs)
    touches = sum(1 for high in highs if abs(high - top) / top <= tolerance)
    return touches >= 3 and candles[-1].close < top * (1 - tolerance / 2)


def higher_low(candles: list[Candle]) -> bool:
    if len(candles) < 12:
        return False
    lows = [candle.low for candle in candles[-12:]]
    return min(lows[-4:]) > min(lows[:4])


def macd_divergence(candles: list[Candle], side: Side) -> bool:
    if len(candles) < 35:
        return False
    closes = [candle.close for candle in candles]
    macd = macd_series(closes)
    recent = candles[-20:]
    midpoint = len(recent) // 2
    first_half = recent[:midpoint]
    second_half = recent[midpoint:]
    first_macd = macd[-20:-10]
    second_macd = macd[-10:]

    if side == "long":
        return min(candle.low for candle in second_half) < min(candle.low for candle in first_half) and min(second_macd) > min(first_macd)
    return max(candle.high for candle in second_half) > max(candle.high for candle in first_half) and max(second_macd) < max(first_macd)


def near_level(price: float, level: float, tolerance: float = 0.015) -> bool:
    if level <= 0:
        return False
    return abs(price - level) / level <= tolerance


def risk_reward(entry: float, stop: float, target: float, side: Side) -> float:
    if side == "long":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    if risk <= 0:
        return 0.0
    return reward / risk


def build_trade_plan(candles: list[Candle], side: Side, confidence_threshold: int = 70) -> TradePlan | None:
    if len(candles) < 30:
        return None

    entry = candles[-1].close
    support, resistance = support_resistance(candles)
    stop = entry * 0.95 if side == "long" else entry * 1.05
    target = resistance if side == "long" else support
    rr = risk_reward(entry, stop, target, side)

    confidence = 35
    reasons: list[str] = []
    setup = "观察"

    if side == "long":
        broke_resistance = entry >= resistance * 0.995
        held_support = near_level(entry, support, 0.035) and entry > support
        if broke_resistance:
            confidence += 18
            reasons.append("突破或贴近压力位")
            setup = "突破压力位做多"
        if held_support:
            confidence += 14
            reasons.append("靠近支撑位且未跌破")
            setup = "支撑不破做多"
        if bullish_engulfing(candles):
            confidence += 14
            reasons.append("出现看涨吞没")
        if morning_star(candles):
            confidence += 14
            reasons.append("出现启明星结构")
        if higher_low(candles):
            confidence += 8
            reasons.append("回踩低点抬高")
        if macd_divergence(candles, "long"):
            confidence += 16
            reasons.append("MACD底背离增加看涨置信度")
        add_plan = "回踩压力线且不破位，或从支撑位反弹后回踩低点抬高时加仓"
    else:
        failed_breakout = near_level(entry, resistance, 0.025) and entry < resistance
        if failed_breakout:
            confidence += 18
            reasons.append("无法有效突破压力位")
            setup = "压力位受阻做空"
        if double_top(candles):
            confidence += 16
            reasons.append("出现对子顶/M字顶")
        if multiple_top(candles):
            confidence += 18
            reasons.append("出现多重顶")
        if bearish_piercing(candles):
            confidence += 12
            reasons.append("出现看跌刺穿")
        if bearish_engulfing(candles):
            confidence += 14
            reasons.append("出现看跌吞没")
        if macd_divergence(candles, "short"):
            confidence += 16
            reasons.append("MACD顶背离增加看跌置信度")
        add_plan = "做空策略不主动加仓；若反弹再次受阻压力位，可重新评估新仓"

    if rr > 2 and confidence >= confidence_threshold:
        decision = "可交易"
    elif rr > 2:
        decision = "观察-置信度不足"
    else:
        decision = "不交易-盈亏比不足"

    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        support=support,
        resistance=resistance,
        risk_reward=rr,
        confidence=min(confidence, 100),
        decision=decision,
        setup=setup,
        reasons=reasons or ["形态信号不足"],
        add_plan=add_plan,
    )
