import os
import pickle
import json
from loguru import logger


class IncidentPredictor:
    """
    Класс предиктора, загружающий последнюю обученную версию модели с диска
    и выполняющий прогнозы рисков по текущим метрикам.
    """

    def __init__(
        self, models_dir: str = "models", task_name: str = "incident_prediction"
    ):
        self.models_dir = models_dir
        self.task_name = task_name
        self.model = None
        self.version = 0

    def load_latest_model(self) -> None:
        """Находит последнюю стабильную версию в реестре и загружает файл модели."""
        registry_path = os.path.join(self.models_dir, f"{self.task_name}_registry.json")
        if not os.path.exists(registry_path):
            logger.info(
                "Predictor: Model registry not found. Predictor remains in standby mode."
            )
            return

        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)

            latest_version = registry.get("latest_version", 0)
            if latest_version == 0:
                logger.info(
                    "Predictor: No trained model versions available in registry."
                )
                return

            model_path = os.path.join(
                self.models_dir, f"{self.task_name}_v{latest_version}.pkl"
            )
            if os.path.exists(model_path):
                with open(model_path, "rb") as f:
                    self.model = pickle.load(f)
                self.version = latest_version
                logger.info(
                    f"Predictor: Loaded model version v{latest_version} successfully."
                )
            else:
                logger.warning(
                    f"Predictor: Model file '{model_path}' not found on disk."
                )
        except Exception as e:
            logger.error(f"Predictor: Failed to load latest model: {e}")

    def predict_risk(self, features: dict) -> float:
        """
        Вычисляет вероятность риска инцидента на основе признаков (0.0 - 1.0).
        Если модель не обучена или отсутствует, возвращает безопасный скор 0.0.
        """
        if not self.model:
            return 0.0

        try:
            import pandas as pd

            # Соблюдаем порядок колонок, на которых модель училась
            feature_names = ["cpu", "mem_perc", "restarts", "status_healthy"]
            X = pd.DataFrame(
                [
                    {
                        "cpu": features.get("cpu", 0.0),
                        "mem_perc": features.get("mem_perc", 0.0),
                        "restarts": features.get("restarts", 0),
                        "status_healthy": features.get("status_healthy", 1.0),
                    }
                ]
            )[feature_names]

            probabilities = self.model.predict_proba(X)
            return float(probabilities[0][1])
        except Exception as e:
            logger.error(f"Predictor: Prediction failed: {e}")
            return 0.0