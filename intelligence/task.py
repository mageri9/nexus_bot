from abc import ABC, abstractmethod
import pandas as pd

class MLTask(ABC):
    """
    Базовый интерфейс для создания аналитических и прогнозных моделей машинного обучения.
    """
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def build_dataset(self) -> pd.DataFrame:
        """
        Собирает исторические данные и размечает целевую переменную (target).
        Обязательно сохраняет event_id для сквозной прослеживаемости.
        """
        pass

    @abstractmethod
    def train(self, df: pd.DataFrame) -> None:
        """
        Обучает выбранную модель на подготовленном датасете.
        """
        pass

    @abstractmethod
    def predict(self, features: dict) -> float:
        """
        Вычисляет скор (вероятность риска) для текущего набора признаков.
        """
        pass