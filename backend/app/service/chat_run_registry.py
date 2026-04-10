"""In-memory registry for active chat and deep-research runs."""

from __future__ import annotations

import threading
from collections.abc import Callable


class ChatRunRegistry:
    """Track cancel callbacks for active assistant-message runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_by_message_id: dict[str, Callable[[], None]] = {}

    def register(self, message_id: str, cancel: Callable[[], None]) -> None:
        """Register one active run by assistant message id."""
        with self._lock:
            self._cancel_by_message_id[message_id] = cancel

    def unregister(self, message_id: str) -> None:
        """Forget one active run when it finishes or is cleaned up."""
        with self._lock:
            self._cancel_by_message_id.pop(message_id, None)

    def cancel(self, message_id: str) -> bool:
        """Cancel one active run if it is still registered."""
        with self._lock:
            cancel = self._cancel_by_message_id.get(message_id)
        if cancel is None:
            return False
        cancel()
        return True


_chat_run_registry = ChatRunRegistry()


def get_chat_run_registry() -> ChatRunRegistry:
    """Return the process-local registry of active assistant runs."""
    return _chat_run_registry
