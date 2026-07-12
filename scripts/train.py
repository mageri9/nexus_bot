#!/usr/bin/env python
import os
import sys
import json
import asyncio
import pickle
import pandas as pd
from datetime import datetime, timezone
from sklearn.metrics import precision_recall_curve, auc

# Делаем корень проекта доступным для импортов
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from intelligence.storage import SqliteEventStorage
from intelligence.tasks.incident_prediction import IncidentPredictionTask

MODELS_DIR = "models"
os.makedirs(MODELS_DIR, exist_ok=True)


def load_registry(task_name: str) -> dict:
    registry_path = os.path.join(MODELS_DIR, f"{task_name}_registry.json")
    if os.path.exists(registry_path):
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"latest_version": 0, "history": []}


def save_registry(task_name: str, registry: dict) -> None:
    registry_path = os.path.join(MODELS_DIR, f"{task_name}_registry.json")
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


async def main():
    if len(sys.argv) < 2:
        print("[-] Ошибка: укажите имя задачи.")
        print("Использование: python scripts/train.py <task_name>")
        sys.exit(1)

    task_name = sys.argv[1]
    if task_name != "incident_prediction":
        print(f"[-] Ошибка: неподдерживаемая задача '{task_name}'.")
        sys.exit(1)

    print(f"[+] Инициализация обучения для задачи: {task_name}...")
    storage = SqliteEventStorage()
    task = IncidentPredictionTask(storage)

    # 1. Сбор датасета из SQLite
    df = await task.build_dataset()
    if df.empty or len(df) < 10:
        print(f"[-] Недостаточно данных для обучения (собрано строк: {len(df)}).")
        print("[!] Запустите симуляцию нагрузки или подождите накопления статистики.")
        sys.exit(0)

    # Защитная проверка: для обучения классификатора нужны оба класса (0 и 1)
    unique_targets = df["target"].nunique()
    if unique_targets < 2:
        print(
            "[-] Ошибка: в собранных метриках присутствует только один класс (все 0 или все 1)."
        )
        print(
            "[!] Для обучения классификатора необходимы исторические примеры инцидентов."
        )
        sys.exit(0)

    # 2. Временной сплит (Time-based split)
    # Сортируем строго по времени, чтобы учиться на прошлом и тестировать на будущем
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    split_idx = int(len(df) * 0.8)

    train_df = df.iloc[:split_idx]
    holdout_df = df.iloc[split_idx:]

    print(
        f"[+] Всего записей: {len(df)} (Обучение: {len(train_df)}, Проверка: {len(holdout_df)})"
    )

    # Защитная проверка: после time-split в train_df тоже должны быть оба класса.
    # На старте, пока инцидентов мало, они могут все попасть в holdout (хронологически
    # последние 20%) — тогда CatBoost.fit() упадёт с необработанным исключением.
    if train_df["target"].nunique() < 2:
        print(
            "[-] В обучающей выборке (после time-split) присутствует только один класс."
        )
        print(
            "[!] Нужно больше истории инцидентов, чтобы они попали и в train, и в holdout."
        )
        sys.exit(0)

    # 3. Обучаем новую модель на тренировочной выборке
    task.train(train_df)

    # 4. Считаем метрику PR-AUC новой модели на проверочной (holdout) выборке
    y_true = holdout_df["target"].tolist()
    y_pred_proba = []
    for _, row in holdout_df.iterrows():
        features = {
            "cpu": row["cpu"],
            "mem_perc": row["mem_perc"],
            "restarts": row["restarts"],
            "status_healthy": row["status_healthy"],
        }
        y_pred_proba.append(task.predict(features))

    precision, recall, _ = precision_recall_curve(y_true, y_pred_proba)
    new_pr_auc = auc(recall, precision)
    print(f"[+] PR-AUC новой модели на проверочной выборке: {new_pr_auc:.4f}")

    # 5. Сравниваем с предыдущей лучшей моделью
    registry = load_registry(task_name)
    latest_ver = registry["latest_version"]

    should_save = True
    old_pr_auc = 0.0

    if latest_ver > 0:
        old_model_path = os.path.join(MODELS_DIR, f"{task_name}_v{latest_ver}.pkl")
        if os.path.exists(old_model_path):
            try:
                # Загружаем старую модель во временную задачу для сравнения
                with open(old_model_path, "rb") as f:
                    old_model = pickle.load(f)

                # Считаем PR-AUC старой модели на той же holdout-выборке
                from catboost import CatBoostClassifier

                temp_task = IncidentPredictionTask(storage)
                temp_task.model = old_model

                y_pred_old = []
                for _, row in holdout_df.iterrows():
                    features = {
                        "cpu": row["cpu"],
                        "mem_perc": row["mem_perc"],
                        "restarts": row["restarts"],
                        "status_healthy": row["status_healthy"],
                    }
                    y_pred_old.append(temp_task.predict(features))

                prec_old, rec_old, _ = precision_recall_curve(y_true, y_pred_old)
                old_pr_auc = auc(rec_old, prec_old)
                print(
                    f"[i] PR-AUC предыдущей модели v{latest_ver} на holdout: {old_pr_auc:.4f}"
                )

                if new_pr_auc < old_pr_auc:
                    print("[-] Отклонено: Новая модель уступает старой по качеству.")
                    should_save = False
            except Exception as ex:
                print(
                    f"[!] Предупреждение при оценке старой модели: {ex}. Перезаписываем новую."
                )

    # 6. Сохранение результатов при успешном прохождении проверок
    if should_save:
        new_version = latest_ver + 1
        new_model_path = os.path.join(MODELS_DIR, f"{task_name}_v{new_version}.pkl")

        # Сохраняем модель через стандартный pickle
        with open(new_model_path, "wb") as f:
            pickle.dump(task.model, f)

        # Обновляем реестр версий
        registry["latest_version"] = new_version
        registry["history"].append(
            {
                "version": new_version,
                "pr_auc": round(new_pr_auc, 4),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "train_size": len(train_df),
                "holdout_size": len(holdout_df),
            }
        )
        save_registry(task_name, registry)
        print(
            f"[+] Успех! Обучена и сохранена модель версии v{new_version} -> {new_model_path}"
        )
    else:
        print("[i] Сохранение отменено. Текущая рабочая версия на диске остается прежней.")

if __name__ == "__main__":
    asyncio.run(main())