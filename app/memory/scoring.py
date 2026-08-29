"""重要性评分：可解释的加权公式。

分数 = w1·任务相关性 + w2·同类频率 + w3·新异性 + w4·用户信号 + w5·时间衰减×基准 + w6·引用加成
权重存 Setting 表（imp_w1..imp_w6），设置页可见可改。
"""
from __future__ import annotations

from datetime import datetime, timedelta

WEIGHT_DEFAULTS = {"w1": 0.20, "w2": 0.15, "w3": 0.15, "w4": 0.20, "w5": 0.15, "w6": 0.15}

_KEYS = ("imp_w1", "imp_w2", "imp_w3", "imp_w4", "imp_w5", "imp_w6")


def get_weights(db) -> dict:
    from app.core.settings_store import get_setting

    w = dict(WEIGHT_DEFAULTS)
    for i, key in enumerate(_KEYS, start=1):
        try:
            v = float(get_setting(db, key, WEIGHT_DEFAULTS[f"w{i}"]))
        except (TypeError, ValueError):
            v = WEIGHT_DEFAULTS[f"w{i}"]
        w[f"w{i}"] = v
    return w


def save_weights(db, weights: dict) -> None:
    from app.core.settings_store import set_setting

    for i, key in enumerate(_KEYS, start=1):
        try:
            v = max(0.0, min(float(weights.get(f"w{i}", WEIGHT_DEFAULTS[f"w{i}"])), 1.0))
        except (TypeError, ValueError):
            v = WEIGHT_DEFAULTS[f"w{i}"]
        set_setting(db, key, str(v))


def _time_decay(created_at: datetime | None, now: datetime) -> float:
    if created_at is None:
        return 1.0
    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return 1.0 if age_days <= 30 else max(0.6, 1.0 - (age_days - 30) * 0.004)


def score_importance(
    base: float,
    created_at: datetime | None,
    now: datetime | None = None,
    ref_count: int = 0,
    task_relevant: float = 0.0,
    frequency: float = 0.0,
    novelty: float = 0.5,
    user_signal: float = 0.0,
    weights: dict | None = None,
) -> float:
    """完整 w1-w6 公式。各分量取值 0-1。"""
    now = now or datetime.now()
    w = {**WEIGHT_DEFAULTS, **(weights or {})}
    decay = _time_decay(created_at, now)
    ref_comp = min(max(ref_count, 0) / 10.0, 1.0)
    score = (
        w["w1"] * min(max(task_relevant, 0.0), 1.0)
        + w["w2"] * min(max(frequency, 0.0), 1.0)
        + w["w3"] * min(max(novelty, 0.0), 1.0)
        + w["w4"] * min(max(user_signal, 0.0), 1.0)
        + w["w5"] * min(max(base, 0.0), 1.0) * decay
        + w["w6"] * ref_comp
    )
    return round(min(max(score, 0.0), 1.0), 3)
