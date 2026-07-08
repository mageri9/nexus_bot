from typing import Dict
from core.resource import Resource


class ProjectAgent:
    def __init__(self, name: str, resources: Dict[str, Resource]):
        self.name = name
        self.resources = resources