import functools
import http.server
import threading
import time
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Self

import watchdog.events
import watchdog.observers

type Signal = tuple[str, Path]


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


class Processor[T]:
    def __init__(
        self,
        name: str,
        patterns: Collection[Path],
        on_signal: Callable[['Core[T]', Signal], None],
    ) -> None:
        self.name = name
        self.patterns = patterns
        self.on_signal = on_signal

    def match(self, path: Path, base: Path = Path('.')) -> bool:
        return any(
            path.full_match(base / pattern) for pattern in self.patterns
        )


class Core[T]:
    def __init__(
        self,
        processors: Sequence[Processor[T]],
        state: T,
        base_path: Path | None = None,
        serve_path: Path | None = None,
    ) -> None:
        self.processors = processors
        self.state = state
        self.base_path = base_path or Path('.')
        self.serve_path = serve_path or Path('.')

    def send_signal(self, signal: Signal) -> None:
        for proc in self.processors:
            proc.on_signal(self, signal)

    def receive_event(self, path: Path, event_type: str) -> None:
        for proc in self.processors:
            if proc.match(path, self.base_path):
                self.send_signal((f'{proc.name}:{event_type}', path))

    def send_events_for_existing_files(self) -> None:
        for base, _, files in self.base_path.walk():
            for file in files:
                self.receive_event(base / file, 'update')

    def start_observe(self) -> None:
        self._observer = watchdog.observers.Observer()
        self._observer.schedule(
            EventToCoreHandler(self), str(self.base_path), recursive=True
        )
        self._observer.start()

    def stop_observe(self) -> None:
        self._observer.stop()
        self._observer.join()

    def start_serve(self) -> None:
        self._httpd = http.server.ThreadingHTTPServer(
            ('0.0.0.0', 5000),
            functools.partial(
                http.server.SimpleHTTPRequestHandler, directory=self.serve_path
            ),
        )
        self._httpd_thread = threading.Thread(target=self._httpd.serve_forever)
        self._httpd_thread.start()

    def stop_serve(self) -> None:
        self._httpd.shutdown()
        self._httpd_thread.join()

    def __enter__(self) -> Self:
        self.start_observe()
        self.start_serve()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        _ = exc_type, exc_value, traceback
        self.stop_observe()
        self.stop_serve()

    def run(self) -> None:
        self.send_events_for_existing_files()
        with self:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print('Exiting')
