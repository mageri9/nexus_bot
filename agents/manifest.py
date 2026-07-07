from core.agent import ProjectAgent
from transports.local_shell import LocalShellTransport
from infra.docker import DockerContainer
from infra.disk import DiskResource

# Общий локальный транспорт для ресурсов
local_transport = LocalShellTransport()

# Описываем агента ImageBot: у него есть Docker-контейнер и локальный диск для кэша картинок
imagebot_agent = ProjectAgent(
    name="imagebot",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-test"),
        "storage": DiskResource("storage", local_transport, "/tmp")
    }
)