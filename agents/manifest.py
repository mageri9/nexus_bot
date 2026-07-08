from core import ProjectAgent
from transports import LocalShellTransport
from infra import DockerContainer, DiskResource

local_transport = LocalShellTransport()

# Проект 1: ImageBot
imagebot_agent = ProjectAgent(
    name="imagebot",
    resources={
        "app": DockerContainer("app", local_transport, "nexus-test"),
        "storage": DiskResource("storage", local_transport, "/tmp")
    }
)

# Проект 2: Commit Chronicle (Новый проект!)
chronicle_agent = ProjectAgent(
    name="chronicle",
    resources={
        "root_disk": DiskResource("root_disk", local_transport, "/host_root")
    }
)