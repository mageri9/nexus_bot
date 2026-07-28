import json
from datetime import datetime, timezone

from loguru import logger


class AppAutoRecoveryService:
    def __init__(
        self, redis_client, event_bus, recovery_threshold_seconds: int = 60
    ):
        self.redis = redis_client
        self.event_bus = event_bus
        self.threshold = recovery_threshold_seconds

    async def check_and_recover(self) -> None:
        """Resolve stale application-error incidents and publish recovery events."""
        try:
            async for key in self.redis.scan_iter("nexus:incident:active:*:app"):
                parts = key.split(":")
                if len(parts) < 5:
                    continue
                project = parts[3]
                incident_id = await self.redis.get(key)
                if not incident_id:
                    continue
                detail_raw = await self.redis.get(
                    f"nexus:incident:detail:{incident_id}"
                )
                if not detail_raw:
                    continue
                fingerprint = json.loads(detail_raw).get("fingerprint")
                if not fingerprint:
                    continue
                err_data = await self.redis.hgetall(f"nexus:errors:{fingerprint}")
                last_seen_str = err_data.get("last_seen") if err_data else None
                if not last_seen_str:
                    continue
                gap = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(last_seen_str)
                ).total_seconds()
                if gap <= self.threshold:
                    continue
                logger.info(
                    f"Auto-Recovery: App exception '{fingerprint}' for '{project}' did not "
                    f"repeat for {gap:.1f}s. Resolving incident #{incident_id}."
                )
                await self.redis.hset(
                    f"nexus:errors:{fingerprint}", "resolved", "1"
                )
                await self.event_bus.publish(
                    "ResourceRecovered", {"agent": project, "resource": "app"}
                )
        except Exception as exc:
            logger.error(f"Error during app auto-recovery execution: {exc}")
