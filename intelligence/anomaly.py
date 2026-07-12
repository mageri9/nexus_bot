import math
from typing import List, Any, Tuple

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


def check_anomaly(current: float, history: List[float], threshold: float = 3.0) -> Tuple[bool, float, float]:
    """
    Вычисляет Z-score для текущего значения на основе истории.
    Возвращает кортеж (is_anomaly, mean, std).
    """
    # Для расчета среднего и стандартного отклонения нужно хотя бы 3 точки в истории
    if not history or len(history) < 3:
        return False, 0.0, 0.0

    n = len(history)
    mean = sum(history) / n

    # Вычисляем выборочную дисперсию и стандартное отклонение (std)
    variance = sum((x - mean) ** 2 for x in history) / (n - 1)
    std = math.sqrt(variance)

    # Защита от деления на ноль, если все прошлые показатели нагрузки были одинаковыми
    if std < 1e-6:
        return False, mean, std

    z_score = abs(current - mean) / std
    return z_score > threshold, mean, std