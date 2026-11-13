import asyncio
import threading
from collections.abc import Callable, Coroutine

from .ask import recall
from .seed import seed


class Memory:
    def __init__(self, dataset: str):
        self.dataset = dataset
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="cognee", daemon=True).start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, make: Callable[[], Coroutine]):
        with self._lock:
            return asyncio.run_coroutine_threadsafe(make(), self._loop).result()

    def seed(self, reset: bool) -> list[str]:
        return self._call(lambda: seed(reset, self.dataset))

    def remember(self, text: str) -> None:
        import cognee

        self._call(lambda: cognee.remember(text, dataset_name=self.dataset, self_improvement=False))

    def remember_later(self, text: str, name: str) -> None:
        threading.Thread(target=self.remember, args=(text,), name=name, daemon=True).start()

    def recall(self, query: str | None = None) -> str:
        return self._call(lambda: recall(self.dataset, query))
