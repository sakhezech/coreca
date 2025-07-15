import contextlib
import functools
import http.server
import threading
import time
from collections.abc import Callable, Collection, Generator, Sequence
from pathlib import Path
from typing import Any, Concatenate, ContextManager, Literal

import watchdog.events
import watchdog.observers


class Signal:
    def __init__(
        self, source: str, type_: Literal['update', 'delete'], path: Path
    ) -> None:
        self.source = source
        # NOTE: without specifying the type again self.type becomes str
        self.type: Literal['update', 'delete'] = type_
        self.path = path


class EventToCoreHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, core: 'Core') -> None:
        self.core = core

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(str(event.src_path))
        if event.event_type in ('created', 'modified'):
            self.core.receive_event(src_path, 'update')
        elif event.event_type in ('deleted',):
            self.core.receive_event(src_path, 'delete')
        elif event.event_type in ('moved',):
            dest_path = Path(str(event.dest_path))
            self.core.receive_event(src_path, 'delete')
            self.core.receive_event(dest_path, 'update')
        else:
            pass


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

    def receive_event(
        self, path: Path, event_type: Literal['update', 'delete']
    ) -> None:
        for proc in self.processors:
            if proc.match(path, self.base_path):
                self.send_signal(Signal(proc.name, event_type, path))

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


@contextlib.contextmanager
def observe(core: Core) -> Generator[None]:
    observer = watchdog.observers.Observer()
    observer.schedule(
        EventToCoreHandler(core), str(core.base_path), recursive=True
    )
    observer.start()
    yield
    observer.stop()
    observer.join()


@contextlib.contextmanager
def serve(
    _: Core, serve_path: Path, host_port: tuple[str, int]
) -> Generator[None]:
    httpd = http.server.ThreadingHTTPServer(
        host_port,
        functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=serve_path
        ),
    )
    httpd_thread = threading.Thread(target=httpd.serve_forever)
    httpd_thread.start()
    yield
    httpd.shutdown()
    httpd_thread.join()
