import contextlib
import functools
import http.server
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Literal

import watchdog.events
import watchdog.observers

from . import Core, Signal


class _CorecaObserveHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, core: 'Core') -> None:
        self.core = core

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(str(event.src_path))
        if event.event_type in ('created', 'modified'):
            self.send_signals_to_core(src_path, 'update')
        elif event.event_type in ('deleted',):
            self.send_signals_to_core(src_path, 'delete')
        elif event.event_type in ('moved',):
            dest_path = Path(str(event.dest_path))
            self.send_signals_to_core(src_path, 'delete')
            self.send_signals_to_core(dest_path, 'update')
        else:
            pass

    def send_signals_to_core(
        self, path: Path, event_type: Literal['update', 'delete']
    ) -> None:
        for proc in self.core.processors:
            if proc.match(path, self.core.base_path):
                self.core.send_signal(Signal(proc.name, event_type, path))


@contextlib.contextmanager
def observe(core: Core) -> Generator[None]:
    observer = watchdog.observers.Observer()
    observer.schedule(
        _CorecaObserveHandler(core), str(core.base_path), recursive=True
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
