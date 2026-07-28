from typing import Dict, Mapping
from core.resource import Resource


class ProjectAgent:
    def __init__(self, name: str, resources: Dict[str, Resource]):
        self.name = name
        self.resources = resources

    def resolve_resource_name(self, resource_name: str) -> str:
        """Return a configured resource name, resolving the app/bot alias."""
        return self.resolve_resource_alias(resource_name, self.resources)

    @staticmethod
    def resolve_resource_alias(
        resource_name: str, resources: Mapping[str, object]
    ) -> str:
        """Resolve the app/bot alias against an arbitrary resource mapping."""
        if resource_name in resources:
            return resource_name
        if resource_name == "app" and "bot" in resources:
            return "bot"
        if resource_name == "bot" and "app" in resources:
            return "app"
        return resource_name
