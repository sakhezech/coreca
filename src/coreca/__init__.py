import contextlib
import functools
import http.server
import threading
import time
from collections.abc import Callable, Collection, Generator, Sequence
from pathlib import Path
from typing import Literal

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
        state: T,
        base_path: Path | None = None,
        serve_path: Path | None = None,
    ) -> None:
        self.processors = processors
        self.signal_handlers = signal_handlers
        self.state = state
        self.base_path = base_path or Path('.')
        self.serve_path = serve_path or Path('.')

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

    @contextlib.contextmanager
    def observe(self) -> Generator[None]:
        self._observer = watchdog.observers.Observer()
        self._observer.schedule(
            EventToCoreHandler(self), str(self.base_path), recursive=True
        )
        self._observer.start()
        yield
        self._observer.stop()
        self._observer.join()

    @contextlib.contextmanager
    def serve(self, host_port: tuple[str, int]) -> Generator[None]:
        self._httpd = http.server.ThreadingHTTPServer(
            host_port,
            functools.partial(
                http.server.SimpleHTTPRequestHandler, directory=self.serve_path
            ),
        )
        self._httpd_thread = threading.Thread(target=self._httpd.serve_forever)
        self._httpd_thread.start()
        yield
        self._httpd.shutdown()
        self._httpd_thread.join()

    def run(self, host_port: tuple[str, int] | None = None) -> None:
        lifespans = [
            self.observe(),
            self.serve(host_port or ('localhost', 5000)),
        ]
        with contextlib.ExitStack() as stack:
            for lifespan in lifespans:
                stack.enter_context(lifespan)
            self.backfill_signals()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print('Exiting')
