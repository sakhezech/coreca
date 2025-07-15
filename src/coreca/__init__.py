import contextlib
import time
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any, Concatenate, ContextManager, Literal


class Signal:
    def __init__(
        self, source: str, type: Literal['update', 'delete'], path: Path
    ) -> None:
        self.source = source
        # NOTE: without specifying the type again self.type becomes str
        self.type: Literal['update', 'delete'] = type
        self.path = path


class Processor:
    def __init__(self, name: str, patterns: Collection[Path]) -> None:
        self.name = name
        self.patterns = patterns

    def match(self, path: Path, base: Path = Path('.')) -> bool:
        return any(
            path.full_match(base / pattern) for pattern in self.patterns
        )


class Core[T]:
    def __init__(
        self,
        processors: Sequence[Processor],
        signal_handlers: Sequence[Callable[['Core[T]', Signal], bool | None]],
        lifespans: Sequence['Lifespan[T, Any]'],
        state: T,
        base_path: Path | None = None,
    ) -> None:
        self.processors = processors
        self.signal_handlers = signal_handlers
        self.lifespans = lifespans
        self.state = state
        self.base_path = base_path or Path('.')

    def send_signal(self, signal: Signal) -> None:
        for handler in self.signal_handlers:
            stop = handler(self, signal)
            if stop:
                return

    def backfill_signals(self) -> None:
        matched_signals: dict[Processor, list[Signal]] = {
            proc: [] for proc in self.processors
        }
        for base, _, files in self.base_path.walk():
            for file in files:
                path = base / file
                for proc, signals in matched_signals.items():
                    if proc.match(path, self.base_path):
                        signals.append(Signal(proc.name, 'update', path))
        for signals in matched_signals.values():
            for signal in signals:
                self.send_signal(signal)

    def run(self) -> None:
        with contextlib.ExitStack() as stack:
            for lifespan in self.lifespans:
                stack.enter_context(lifespan(self))
            self.backfill_signals()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print('Exiting')


class Lifespan[T, **P]:
    def __init__(
        self,
        lifespan: Callable[Concatenate[Core[T], P], ContextManager],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        self.lifespan = lifespan
        self.args = args
        self.kwargs = kwargs

    def __call__(self, core: Core[T]) -> ContextManager:
        return self.lifespan(core, *self.args, **self.kwargs)
