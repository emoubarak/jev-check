"""jevcheck: a browser agent reads your staging app as a chosen account state, and every
answer is checked against the database — never against the model's own "done"."""

from .checks import Check, Ctx, actions, final_url, has_any, offered, result, status, text_of
from .store import lit

__all__ = ["Check", "Ctx", "actions", "final_url", "has_any", "lit", "offered", "result", "status", "text_of"]
__version__ = "1.0.0"
