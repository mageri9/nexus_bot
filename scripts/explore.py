#!/usr/bin/env python
import os
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta, timezone

DB_PATH = "data/events.db"


def check_database() -> bool:
    if not os.path.exists(DB_PATH):
        print(f"[-] База данных не найдена по пути: {DB_PATH}")
        print(
            "[!] Пожалуйста, запустите Nexus и подождите несколько минут, чтобы накопились данные."
        )
        return False
    return True


def run_analysis():
    print("=" * 60)
    print("📡 NEXUS SRE OFFLINE ANALYSIS TOOL")
    print("=" * 60)

    # Устанавливаем соединение с базой данных SQLite
    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    # 1. Загружаем таблицы в Pandas DataFrame
    df_events = pd.read_sql_query("SELECT * FROM event_log", conn)
    df_metrics = pd.read_sql_query("SELECT * FROM metric_snapshots", conn)

    # Приводим даты к правильному формату
    df_events["timestamp"] = pd.to_datetime(df_events["timestamp"])
    if not df_metrics.empty:
        df_metrics["timestamp"] = pd.to_datetime(df_metrics["timestamp"])

    print(f"[+] Успешно загружено событий из лога: {len(df_events)}")
    print(f"[+] Успешно загружено снимков метрик  : {len(df_metrics)}")
    print("-" * 60)

    if df_events.empty:
        print("[!] Таблица событий пуста. Анализ невозможен.")
        return

    # === АНАЛИЗ 1: Количество инцидентов по проектам ===
    print("\n📊 1. КОЛИЧЕСТВО ИНЦИДЕНТОВ ПО ПРОЕКТАМ:")
    df_incidents = df_events[df_events["event_type"] == "incident:opened"]
    if not df_incidents.empty:
        incident_counts = (
            df_incidents.groupby("project").size().reset_index(name="incidents_count")
        )
        print(incident_counts.to_string(index=False))
    else:
        print("  [i] Инциденты не зарегистрированы.")

    # === АНАЛИЗ 2: Топ падающих ресурсов ===
    print("\n🔥 2. ТОП ПРОБЛЕМНЫХ РЕСУРСОВ (ПО ВСЕМ АВАРИЙНЫМ СОБЫТИЯМ):")
    # Отбираем события падений, высокой или средней критичности
    failing_events = df_events[
        df_events["event_type"].isin(
            ["ResourceStopped", "ResourceUnhealthy", "app:error"]
        )
    ]
    if not failing_events.empty:
        top_failing = (
            failing_events.groupby(["project", "resource"])
            .size()
            .reset_index(name="failures_count")
        )
        top_failing = top_failing.sort_values(by="failures_count", ascending=False)
        print(top_failing.to_string(index=False))
    else:
        print("  [i] Событий падения или деградации ресурсов не обнаружено.")

    # === АНАЛИЗ 3: Корреляция деплой -> инцидент в течение 30 минут ===
    print("\n🚀 3. АНАЛИЗ КОРРЕЛЯЦИИ 'ДЕПЛОЙ -> АВАРИЯ В ТЕЧЕНИЕ 30 МИНУТ':")
    deploys = df_events[df_events["event_type"].str.startswith("devops:workflow_")]
    incidents = df_events[df_events["event_type"] == "incident:opened"]

    correlations = []
    if not deploys.empty and not incidents.empty:
        for _, deploy in deploys.iterrows():
            project = deploy["project"]
            deploy_time = deploy["timestamp"]

            # Ищем инциденты по тому же проекту в интервале [deploy_time, deploy_time + 30 минут]
            matching_incidents = incidents[
                (incidents["project"] == project)
                & (incidents["timestamp"] >= deploy_time)
                & (incidents["timestamp"] <= deploy_time + pd.Timedelta(minutes=30))
            ]

            for _, inc in matching_incidents.iterrows():
                # Пытаемся вытащить причину падения из JSON-полезной нагрузки
                reason = "Неизвестно"
                try:
                    payload = json.loads(inc["payload_json"])
                    reason = payload.get("reason", "N/A")
                except Exception:
                    pass

                delay_mins = (inc["timestamp"] - deploy_time).total_seconds() / 60.0
                correlations.append(
                    {
                        "Проект": project,
                        "Время релиза": deploy_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "Время аварии": inc["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                        "Причина": reason,
                        "Задержка (мин)": round(delay_mins, 1),
                    }
                )

    if correlations:
        df_corr = pd.DataFrame(correlations)
        print(df_corr.to_string(index=False))
    else:
        print(
            "  [i] Не обнаружено инцидентов, произошедших сразу после деплоя (в пределах 30 минут)."
        )

    # === АНАЛИЗ 4: Предупреждения средней и высокой критичности за последние 24 часа ===
    print("\n⚠️ 4. СВЕЖИЕ ПРЕДУПРЕЖДЕНИЯ (SEVERITY >= WARNING/HIGH) ЗА 24 ЧАСА:")
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    warnings_last_24h = df_events[
        (df_events["timestamp"] >= pd.to_datetime(yesterday))
        & (df_events["severity"].isin(["WARNING", "HIGH", "MEDIUM"]))
    ]

    if not warnings_last_24h.empty:
        warnings_last_24h = warnings_last_24h.sort_values(
            by="timestamp", ascending=False
        )
        output_cols = ["timestamp", "project", "resource", "severity", "event_type"]
        print(warnings_last_24h[output_cols].to_string(index=False))
    else:
        print("  [i] За последние 24 часа критических предупреждений не зафиксировано.")

    conn.close()
    print("=" * 60)

if __name__ == "__main__":
    if check_database():
        run_analysis()
