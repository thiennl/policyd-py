from importlib import import_module

__all__ = [
    "ActionManager",
    "ManagerConfig",
    "ActionContext",
    "ActionProvider",
    "ActionResult",
    "ActionType",
    "ScriptActionProvider",
]


def __getattr__(name: str):
    if name in {"ActionManager", "ManagerConfig"}:
        module = import_module("policyd_py.actions.manager")
        return getattr(module, name)
    if name in {"ActionContext", "ActionProvider", "ActionResult", "ActionType"}:
        module = import_module("policyd_py.actions.provider")
        return getattr(module, name)
    if name == "ScriptActionProvider":
        module = import_module("policyd_py.actions.script_provider")
        return getattr(module, name)
    raise AttributeError(name)
