from abc import abstractmethod
from typing import TypeVar

import autogen_core

T = TypeVar("T")


class CacheStore(autogen_core.CacheStore[T]):
    @abstractmethod
    def delete(self, key: str) -> None: ...
