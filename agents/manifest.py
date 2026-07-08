from core import ProjectAgent
from transports import LocalShellTransport
from infra import DockerContainer, DiskResource

local_transport = LocalShellTransport()

# Проект 1: ImageBot (Команда, база данных, кэш)
imagebot_agent = ProjectAgent(
    name="imagebot",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-test-imagebot-app"),
        "postgres": DockerContainer("postgres", local_transport, "nexus-test-imagebot-db"),
        "redis": DockerContainer("redis", local_transport, "nexus-test-imagebot-redis")
    }
)

# Проект 2: Commit Chronicle (Приложение и мониторинг системного диска)
chronicle_agent = ProjectAgent(
    name="chronicle",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-test-chronicle-app"),
        "root_disk": DiskResource("root_disk", local_transport, "/host_root")
    }
)

# Проект 3: Skillbook (Приложение и выделенная БД)
skillbook_agent = ProjectAgent(
    name="skillbook",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-test-skillbook-app"),
        "postgres": DockerContainer("postgres", local_transport, "nexus-test-skillbook-db")
    }
)

# Проект 4: Nexus (Мониторинг управляющего центра)
nexus_agent = ProjectAgent(
    name="nexus",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-core"),
        "root_disk": DiskResource("root_disk", local_transport, "/host_root")
    }
)