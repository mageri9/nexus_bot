import json
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from intelligence.task import MLTask
from intelligence.storage import EventStorage


class IncidentPredictionTask(MLTask):
    """
    Задача прогнозирования риска открытия инцидента на основе метрик.
    Целевая переменная (target): откроется ли авария по этому ресурсу в течение ближайших 30 минут.
    """

    def __init__(self, storage: EventStorage):
        super().__init__(name="incident_prediction")
        self.storage = storage
        self.model = None

    async def build_dataset(self) -> pd.DataFrame:
        snapshots = await self.storage.query_all_metric_snapshots(limit=10000)
        events = await self.storage.query(limit=10000, event_type="incident:opened")

        if not snapshots:
            return pd.DataFrame()

        # Трансформируем инциденты для ускорения поиска
        incident_list = []
        for ev in events:
            incident_list.append(
                {
                    "project": ev.project,
                    "resource": ev.resource,
                    "timestamp": ev.timestamp,
                }
            )
        df_incidents = pd.DataFrame(incident_list)

        data_rows = []
        from intelligence.anomaly import parse_float_metric

        for snap in snapshots:
            snap_time = snap.timestamp
            project = snap.agent
            resource = snap.resource

            # Разметка целевой переменной: ищем инцидент в интервале [snap_time, snap_time + 30 мин]
            target = 0.0
            if not df_incidents.empty:
                window_end = snap_time + timedelta(minutes=30)
                matching = df_incidents[
                    (df_incidents["project"] == project)
                    & (df_incidents["resource"] == resource)
                    & (df_incidents["timestamp"] >= snap_time)
                    & (df_incidents["timestamp"] <= window_end)
                ]
                if not matching.empty:
                    target = 1.0

            cpu_val = parse_float_metric(snap.cpu) or 0.0
            mem_val = parse_float_metric(snap.mem_perc) or 0.0
            restarts_val = snap.restarts or 0

            data_rows.append(
                {
                    "event_id": snap.snapshot_id,  # Уникальный идентификатор исходной точки
                    "timestamp": snap_time,
                    "project": project,
                    "resource": resource,
                    "cpu": cpu_val,
                    "mem_perc": mem_val,
                    "restarts": restarts_val,
                    "status_healthy": 1.0
                    if snap.status in ("running", "healthy")
                    else 0.0,
                    "target": target,
                }
            )

        return pd.DataFrame(data_rows)

    def train(self, df: pd.DataFrame) -> None:
        """
        Ленивый импорт CatBoost для предотвращения падения ядра
        при запуске на серверах без установленных ML-библиотек.
        """
        if df.empty:
            return
        from catboost import CatBoostClassifier

        features = ["cpu", "mem_perc", "restarts", "status_healthy"]
        X = df[features]
        y = df["target"]

        # Инициализация и обучение легковесной модели градиентного бустинга
        self.model = CatBoostClassifier(
            iterations=100, depth=4, learning_rate=0.1, verbose=0
        )
        self.model.fit(X, y)

    def predict(self, features: dict) -> float:
        if not self.model:
            return 0.0  # Дефолтный безопасный скор, если модель еще не обучена

        import pandas as pd

        feature_names = ["cpu", "mem_perc", "restarts", "status_healthy"]

        # Подготовка данных с жестким соблюдением структуры признаков
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

        # Возвращаем вероятность наступления аварии (класс 1)
        probabilities = self.model.predict_proba(X)
        return float(probabilities[0][1])