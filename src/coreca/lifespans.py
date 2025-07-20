import contextlib
import functools
import http.server
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Literal

import watchdog.events
import watchdog.observers
import websockets.exceptions
import websockets.sync.server

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


@contextlib.contextmanager
def websocket[T](
    core: 'Core[T]',
    host_port: tuple[str, int],
    handler: Callable[['Core[T]', 'Signal'], str],
) -> Generator[None]:
    _msg = None

    def websocket_handler(ws: websockets.sync.server.ServerConnection) -> None:
        nonlocal _msg
        try:
            while True:
                if _msg:
                    ws.send(_msg)
                    _msg = None
                time.sleep(0.1)
        except websockets.exceptions.ConnectionClosed:
            pass

    def signal_handler(core: 'Core[T]', signal: 'Signal') -> None:
        nonlocal _msg
        _msg = handler(core, signal)

    wsd = websockets.sync.server.serve(websocket_handler, *host_port)
    wsd_thread = threading.Thread(target=wsd.serve_forever)
    wsd_thread.start()
    # HACK: signal_handler is not guarantied to be mutable
    core.signal_handlers.append(signal_handler)
    yield
    core.signal_handlers.remove(signal_handler)
    wsd.shutdown()
    wsd_thread.join()
