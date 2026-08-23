"""Dashboard route adapters for the shared review-fix HTTP service."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_ADAPTER_NAME = "kirocrew_review_fix_http"
_ADAPTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "builtins"
    / "code_review_sage"
    / "backend"
    / "fix_tasks.py"
)


def _adapter() -> ModuleType:
    loaded = sys.modules.get(_ADAPTER_NAME)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(_ADAPTER_NAME, _ADAPTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("review-fix adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ADAPTER_NAME] = module
    spec.loader.exec_module(module)
    return module


async def api_taskrunner_review_fix(request):
    return await _adapter().handle_get_fix_task(request)


async def api_taskrunner_review_fix_actions(request):
    return await _adapter().handle_fix_action(request)
