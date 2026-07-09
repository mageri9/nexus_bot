from abc import ABC, abstractmethod
from typing import Dict, Any


class HealthRule(ABC):
    @abstractmethod
    def evaluate(self, resource_name: str, resource_data: Dict[str, Any]) -> int:
        """Возвращает штрафной балл за нарушение правила"""
        pass


class StatusRule(HealthRule):
    """Штраф за нерабочее состояние сервиса и падение критических баз данных"""

    def evaluate(self, resource_name: str, resource_data: Dict[str, Any]) -> int:
        status = resource_data.get("status", "unknown")
        penalty = 0
        if status not in ("running", "healthy"):
            penalty += 50
            # Дополнительный штраф за падение критической инфраструктуры
            if resource_name.lower() in ("redis", "postgres", "postgresql"):
                penalty += 20
        return penalty


class CpuRule(HealthRule):
    """Штраф за пиковую утилизацию CPU > 95%"""

    def evaluate(self, resource_name: str, resource_data: Dict[str, Any]) -> int:
        metrics = resource_data.get("metrics", {})
        cpu_metric = metrics.get("cpu", {})

        # Поддержка структуры Metric V2 или сырого значения
        cpu_val_raw = (
            cpu_metric.get("value", "0.00%")
            if isinstance(cpu_metric, dict)
            else cpu_metric
        )

        if isinstance(cpu_val_raw, str):
            try:
                cpu_val = float(cpu_val_raw.replace("%", "").strip())
                if cpu_val > 95.0:
                    return 15
            except ValueError:
                pass
        return 0


class MemRule(HealthRule):
    """Штраф за критическое потребление RAM > 90%"""

    def evaluate(self, resource_name: str, resource_data: Dict[str, Any]) -> int:
        metrics = resource_data.get("metrics", {})
        mem_metric = metrics.get("mem_perc", {})

        # Поддержка структуры Metric V2 или сырого значения
        mem_val_raw = (
            mem_metric.get("value", "0.00%")
            if isinstance(mem_metric, dict)
            else mem_metric
        )

        if isinstance(mem_val_raw, str):
            try:
                mem_val = float(mem_val_raw.replace("%", "").strip())
                if mem_val > 90.0:
                    return 15
            except ValueError:
                pass
        return 0


class RestartRule(HealthRule):
    """Штраф за перезапуски контейнера (-5 баллов за каждый рестарт)"""

    def evaluate(self, resource_name: str, resource_data: Dict[str, Any]) -> int:
        metrics = resource_data.get("metrics", {})
        restart_metric = metrics.get("restarts", {})

        # Поддержка структуры Metric V2 или сырого значения
        restarts = (
            restart_metric.get("value", 0)
            if isinstance(restart_metric, dict)
            else restart_metric
        )

        try:
            restarts_val = int(restarts)
            return restarts_val * 5
        except (ValueError, TypeError):
            return 0


class HealthEngine:
    def __init__(self):
        # Реестр активных правил оценки здоровья
        self.rules: list[HealthRule] = [
            StatusRule(),
            CpuRule(),
            MemRule(),
            RestartRule(),
        ]

    def calculate_score(self, state_v2: Dict[str, Any]) -> int:
        """Вычисляет показатель здоровья (0-100) на основе версионированного State V2"""
        if not state_v2 or "error" in state_v2:
            return 0

        # Если стейт старой версии V1 (до обновления)
        if state_v2.get("version") != 2:
            # Возвращаем 0, так как коллектор обновит состояние в течение 5 секунд
            return 100

        score = 100

        # 1. Оценка контейнеров на основе правил
        containers = state_v2.get("containers", {})
        for name, data in containers.items():
            for rule in self.rules:
                score -= rule.evaluate(name, data)

        # 2. Оценка хранилищ
        storage = state_v2.get("storage", {})
        for name, data in storage.items():
            status = data.get("status", "unknown")
            if status not in ("running", "healthy"):
                score -= 50

        return max(0, score)