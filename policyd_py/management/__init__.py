from importlib import import_module

__all__ = ["ManagementAPIServer", "ConfigManager", "ManagementService"]


def __getattr__(name: str):
    if name == "ManagementAPIServer":
        module = import_module("policyd_py.management.api_server")
        return getattr(module, name)
    if name == "ConfigManager":
        module = import_module("policyd_py.management.config_manager")
        return getattr(module, name)
    if name == "ManagementService":
        module = import_module("policyd_py.management.service")
        return getattr(module, name)
    raise AttributeError(name)
