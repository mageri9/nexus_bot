from core import ProjectAgent
from transports import LocalShellTransport
from infra import DockerContainer, HostDiskResource, ProjectStorageResource

local_transport = LocalShellTransport()

# Проект 1: M9 Imagebot
imagebot_agent = ProjectAgent(
    name="imagebot",
    resources={
        "app": DockerContainer("app", local_transport, "m9_imagebot"),
        "redis": DockerContainer("redis", local_transport, "m9_imagebot_redis"),
        "data_disk": ProjectStorageResource("data_disk", local_transport, "/host_root/home/mageri9/apps/m9_imagebot/data")
    }
)

# Проект 2: Tarot Bot
tarot_agent = ProjectAgent(
    name="tarot_bot",
    resources={
        "bot": DockerContainer("bot", local_transport, "tarot_bot"),
        "postgres": DockerContainer("postgres", local_transport, "tarot_bot_postgres"),
        "redis": DockerContainer("redis", local_transport, "tarot_bot_redis"),
        "data_disk": ProjectStorageResource("data_disk", local_transport, "/host_root/home/mageri9/apps/tarot_bot")
    }
)

# Проект 3: Commit Chronicle
chronicle_agent = ProjectAgent(
    name="chronicle",
    resources={
        "bot": DockerContainer("bot", local_transport, "chronicle_bot"),
        "worker": DockerContainer("worker", local_transport, "chronicle_worker"),
        "redis": DockerContainer("redis", local_transport, "chronicle_redis"),
        "data_disk": ProjectStorageResource("data_disk", local_transport, "/host_root/home/mageri9/apps/commit_chronicle/data")
    }
)

# Проект 4: Nexus (Мониторинг самого управляющего центра)
nexus_agent = ProjectAgent(
    name="nexus",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-core"),
        "webhook": DockerContainer("webhook", local_transport, "nexus-webhook"),
        "redis": DockerContainer("redis", local_transport, "nexus-redis"),
        # Мониторим системный диск целиком
        "root_disk": HostDiskResource("root_disk", local_transport, "/host_root")
    }
)