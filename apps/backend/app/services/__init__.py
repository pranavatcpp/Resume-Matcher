"""Business logic services, loaded lazily for lightweight local tools."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "generate_improvements": "improver",
    "improve_resume": "improver",
    "parse_document": "parser",
    "parse_resume_to_json": "parser",
    "refine_resume": "refiner",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(f"app.services.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value
