from loguru import logger

from core.telemetry import extract_metric_value
from intelligence.anomaly import check_anomaly


class AnomalyEvaluator:
    def __init__(self, event_storage, event_bus):
        self.event_storage = event_storage
        self.event_bus = event_bus

    async def evaluate_container_metrics(
        self, agent_name: str, res_name: str, metrics: dict
    ) -> None:
        if not self.event_storage:
            return
        snapshots = await self.event_storage.query_metric_snapshots(
            agent_name, res_name, limit=120
        )
        if not snapshots:
            return

        cpu_history = [
            value
            for snap in snapshots
            if (value := extract_metric_value(snap.cpu)) is not None
        ]
        mem_history = [
            value
            for snap in snapshots
            if (value := extract_metric_value(snap.mem_perc)) is not None
        ]
        current_cpu = (
            extract_metric_value(metrics.get("cpu"))
            if isinstance(metrics, dict)
            else None
        )
        current_mem = (
            extract_metric_value(metrics.get("mem_perc"))
            if isinstance(metrics, dict)
            else None
        )
        await self._publish_if_anomalous(
            agent_name, res_name, "cpu", current_cpu, cpu_history, "CPU"
        )
        await self._publish_if_anomalous(
            agent_name, res_name, "mem_perc", current_mem, mem_history, "RAM"
        )

    async def _publish_if_anomalous(
        self,
        agent_name,
        res_name,
        metric_key,
        current_value,
        history,
        metric_label,
    ) -> None:
        if current_value is None or len(history) < 3:
            return
        is_anomalous, mean, std = check_anomaly(
            current_value, history, metric_key=metric_key
        )
        if not is_anomalous:
            return
        logger.warning(
            f"Anomaly in {metric_label} for {agent_name}:{res_name}: "
            f"current={current_value}%, mean={mean:.2f}%, std={std:.2f}"
        )
        await self.event_bus.publish(
            "ml:anomaly_detected",
            {
                "project": agent_name,
                "resource": res_name,
                "metric": metric_key,
                "current_value": f"{current_value}%",
                "mean": f"{mean:.2f}%",
                "std": f"{std:.2f}",
            },
        )
