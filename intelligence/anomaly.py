import math
from typing import List, Any, Tuple, Optional

# Минимальный "пол" для std, чтобы z-score не взрывался на почти неподвижных
# метриках (когда история почти константна, std -> 0, любое дрожание = "аномалия").
# Значения в тех же единицах, что и сама метрика (обычно %).
DEFAULT_MIN_STD = {
    "cpu": 0.5,
    "mem_perc": 1.0,
    "ram": 1.0,
}

# Минимальный абсолютный сдвиг (в пунктах метрики), ниже которого мы вообще
# не считаем это аномалией, даже если z-score большой. Это убирает случаи вроде
# "redis RAM 0.17% vs 0.15%" — формально далеко от среднего, по факту шум.
DEFAULT_MIN_DELTA = {
    "cpu": 1.0,
    "mem_perc": 2.0,
    "ram": 2.0,
}


def parse_float_metric(value: Any) -> float | None:
    """
    Безопасно преобразует строку процента (например, \"12.34%\") или число во float.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace("%", "").strip())
        except (ValueError, TypeError):
            return None
    return None


def check_anomaly(
    current: float,
    history: List[float],
    threshold: float = 3.0,
    metric_key: Optional[str] = None,
    min_std: Optional[float] = None,
    min_delta: Optional[float] = None,
) -> Tuple[bool, float, float]:
    """
    Вычисляет Z-score для текущего значения на основе истории.

    metric_key используется для подбора дефолтных порогов (MIN_STD/MIN_DELTA),
    если min_std/min_delta не переданы явно. Если ключ метрики неизвестен —
    используются консервативные общие дефолты (0.5 / 1.0), чтобы не открывать
    дыру для неразмеченных метрик.

    Возвращает кортеж (is_anomaly, mean, std).
    """
    # Для расчета среднего и стандартного отклонения нужно хотя бы 3 точки в истории
    if not history or len(history) < 3:
        return False, 0.0, 0.0

    n = len(history)
    mean = sum(history) / n

    # Вычисляем выборочную дисперсию и стандартное отклонение (std)
    variance = sum((x - mean) ** 2 for x in history) / (n - 1)
    raw_std = math.sqrt(variance)

    effective_min_std = min_std if min_std is not None else DEFAULT_MIN_STD.get(metric_key, 0.5)
    effective_min_delta = min_delta if min_delta is not None else DEFAULT_MIN_DELTA.get(metric_key, 1.0)

    # 1. Абсолютный порог: если реальный сдвиг мал в единицах метрики — не аномалия,
    #    независимо от того, что говорит z-score. Это отсекает шум на "тихих" ресурсах.
    delta = abs(current - mean)
    if delta < effective_min_delta:
        return False, mean, raw_std

    # 2. Пол на std: не даём знаменателю схлопнуться в ноль/почти-ноль и
    #    превратить любое дрожание в z-score в десятки сигм.
    effective_std = max(raw_std, effective_min_std)

    z_score = delta / effective_std
    return z_score > threshold, mean, effective_std