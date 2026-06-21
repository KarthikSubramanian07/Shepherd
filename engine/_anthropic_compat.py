"""
Compat shim for Agent S + newer Anthropic models.

gui-agents always sends `temperature` to the Anthropic Messages API, but some newer
models (e.g. claude-opus-4-8) reject it — the API returns
`temperature is deprecated for this model` and Agent S's plan/ground calls all fail.

This monkeypatches the Anthropic SDK's `Messages.create` to drop `temperature`
(and `top_p`, similarly deprecated) for those models. It is idempotent and a safe
no-op when the SDK isn't installed. Applied from AgentSAdapter when the engine is
Anthropic; lives in our repo so it survives `uv sync`/library reinstalls.
"""
import importlib

# Models that reject sampling params. Substring match against the request model id.
_PARAM_DEPRECATED_MODELS = ("claude-opus-4-8",)
_STRIP_PARAMS = ("temperature", "top_p")


def _messages_class():
    for path in ("anthropic.resources.messages.messages",
                 "anthropic.resources.messages",
                 "anthropic.resources"):
        try:
            mod = importlib.import_module(path)
        except Exception:
            continue
        cls = getattr(mod, "Messages", None)
        if cls is not None:
            return cls
    return None


def apply() -> bool:
    """Patch Messages.create to strip deprecated sampling params. Returns True if applied."""
    cls = _messages_class()
    if cls is None:
        return False
    if getattr(cls.create, "_shepherd_patched", False):
        return True

    original = cls.create

    def create(self, *args, **kwargs):
        model = str(kwargs.get("model", ""))
        if any(m in model for m in _PARAM_DEPRECATED_MODELS):
            for p in _STRIP_PARAMS:
                kwargs.pop(p, None)
        return original(self, *args, **kwargs)

    create._shepherd_patched = True
    cls.create = create
    return True
