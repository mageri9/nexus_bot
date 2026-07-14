import math
from typing import List, Any, Tuple, Optional

# Минимальный "пол" для std, чтобы z-score не взрывался на почти неподвижных метриках.
# Увеличили значения для CPU и RAM, чтобы снизить чувствительность к микро-колебаниям.
DEFAULT_MIN_STD = {
    "cpu": 2.0,       # было 0.5
    "mem_perc": 1.5,  # было 1.0
    "ram": 1.5,
}

# Минимальный абсолютный сдвиг, ниже которого аномалия не регистрируется.
# Увеличили пороги, так как фоновые колебания CPU до 10% и RAM до 5% абсолютно естественны.
DEFAULT_MIN_DELTA = {
    "cpu": 10.0,      # было 1.0
    "mem_perc": 5.0,  # было 2.0
    "ram": 5.0,
}


def parse_float_metric(value: Any) -> float | None:
    """
    Безопасно преобразует строку процента (например, "12.34%") или число во float.
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
    Определяет аномалии только при росте показателей (односторонний критерий).
    """
    # Для расчета среднего и стандартного отклонения нужно хотя бы 3 точки в истории
    if not history or len(history) < 3:
        return False, 0.0, 0.0

    n = len(history)
    mean = sum(history) / n

    # Вычисляем выборочную дисперсию и стандартное отклонение (std)
    variance = sum((x - mean) ** 2 for x in history) / (n - 1)
    raw_std = math.sqrt(variance)

    if raw_std < 1e-6:
        return False, mean, raw_std

    # Если ключ неизвестен (например, в тестах передано None), используем консервативные старые дефолты
    effective_min_std = min_std if min_std is not None else DEFAULT_MIN_STD.get(metric_key, 0.5)
    effective_min_delta = min_delta if min_delta is not None else DEFAULT_MIN_DELTA.get(metric_key, 1.0)

    # Односторонняя проверка: нас интересует только аномальный рост потребления.
    # Снижение нагрузки (простой процессора, очистка RAM) игнорируется.
    if current <= mean:
        return False, mean, raw_std

    # Разница без использования abs() — фиксируем только движение вверх
    delta = current - mean
    if delta < effective_min_delta:
        return False, mean, raw_std

    # Пол на std: не даём знаменателю превратить микро-колебания в десятки сигм
    effective_std = max(raw_std, effective_min_std)

    z_score = delta / effective_std
    return z_score > threshold, mean, effective_std