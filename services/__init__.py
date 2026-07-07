from .query import QueryService
from agents import registry

# Создаем готовый инстанс Query API для внешних слоев
query_service = QueryService(registry=registry)