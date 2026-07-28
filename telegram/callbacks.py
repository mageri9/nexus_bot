from aiogram.filters.callback_data import CallbackData


class AgentViewCallback(CallbackData, prefix="ag_view"):
    agent_name: str


class RootMenuCallback(CallbackData, prefix="root_menu"):
    pass


class GlobalStatsCallback(CallbackData, prefix="glob_stats"):
    pass


class AgentActionCallback(CallbackData, prefix="ag_act"):
    agent: str
    resource: str
    action: str
