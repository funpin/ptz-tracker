"""Фальшивый Blackmagic Videohub для тестов (не тестовый модуль): TCP на 127.0.0.1, ведёт себя по описанию
протокола v2.3. Текст собран здесь вручную, независимо от ptztracker.videohub.protocol. Порты внутри — с 0."""

from __future__ import annotations

import socket
import threading
import time


class FakeHub:
    def __init__(self, inputs: int = 20, outputs: int = 20, port: int = 0, model: str = "Smart Videohub 20 x 20"):
        self.inputs, self.outputs, self.model = inputs, outputs, model
        self.routing = {o: 0 for o in range(outputs)}  # с 0
        self.locks = {o: "U" for o in range(outputs)}
        self.routes_received: list[tuple[int, int]] = []  # (выход, вход) с 1 — как в программе
        self.pings = 0
        self.nak_next = False
        self.silent = False  # принимает и читает, но ничего не отвечает
        self._lock = threading.Lock()
        self._clients: list[socket.socket] = []
        self._port = port
        self._srv: socket.socket | None = None
        self._stop = threading.Event()
        self.start()

    # --- управление ---------------------------------------------------

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self._port))
        srv.listen(8)
        srv.settimeout(0.05)
        self._port = srv.getsockname()[1]
        self._srv = srv
        self._stop = threading.Event()
        threading.Thread(target=self._accept, args=(srv, self._stop), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            self._srv.close()
            self._srv = None
        self.drop_clients()

    def restart(self) -> None:
        self.stop()
        time.sleep(0.05)
        self.start()

    def drop_clients(self) -> None:
        with self._lock:
            cs, self._clients = self._clients, []
        for c in cs:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def set_lock(self, output: int, state: str) -> None:
        """output с 1; state — O | L | U. Рассылает обновление, как настоящий хаб."""
        self.locks[output - 1] = state
        self._broadcast(f"VIDEO OUTPUT LOCKS:\n{output - 1} {state}\n\n")

    def route_from_panel(self, output: int, input: int) -> None:
        """Кто-то переключил с другого пульта (с 1)."""
        self.routing[output - 1] = input - 1
        self._broadcast(f"VIDEO OUTPUT ROUTING:\n{output - 1} {input - 1}\n\n")

    def input_on(self, output: int) -> int:
        return self.routing[output - 1] + 1

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    # --- сервер -------------------------------------------------------

    def _dump(self) -> str:
        ins = "".join(f"{i} Вход {i + 1}\n" for i in range(self.inputs))
        outs = "".join(f"{o} Выход {o + 1}\n" for o in range(self.outputs))
        rt = "".join(f"{o} {i}\n" for o, i in self.routing.items())
        lk = "".join(f"{o} {s}\n" for o, s in self.locks.items())
        return ("PROTOCOL PREAMBLE:\nVersion: 2.3\n\n"
                f"VIDEOHUB DEVICE:\nDevice present: true\nModel name: {self.model}\nVideo inputs: {self.inputs}\n"
                f"Video processing units: 0\nVideo outputs: {self.outputs}\nVideo monitoring outputs: 0\n"
                "Serial ports: 0\n\n"
                f"INPUT LABELS:\n{ins}\nOUTPUT LABELS:\n{outs}\nVIDEO OUTPUT LOCKS:\n{lk}\n"
                f"VIDEO OUTPUT ROUTING:\n{rt}\nEND PRELUDE:\n\n")

    def _accept(self, srv: socket.socket, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            with self._lock:
                self._clients.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _send(self, c: socket.socket, text: str) -> None:
        if self.silent:
            return
        try:
            c.sendall(text.encode())
        except OSError:
            pass

    def _broadcast(self, text: str) -> None:
        with self._lock:
            cs = list(self._clients)
        for c in cs:
            self._send(c, text)

    def _serve(self, c: socket.socket) -> None:
        self._send(c, self._dump())
        buf = b""
        lines: list[str] = []
        c.settimeout(0.1)
        while not self._stop.is_set():
            try:
                data = c.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                ln = raw.decode().strip()
                if ln:
                    lines.append(ln)
                elif lines:
                    self._handle(c, lines)
                    lines = []
        with self._lock:
            if c in self._clients:
                self._clients.remove(c)
        c.close()

    def _handle(self, c: socket.socket, lines: list[str]) -> None:
        head = lines[0]
        if head == "PING:":
            self.pings += 1
            self._send(c, "ACK\n\n")
            return
        if head != "VIDEO OUTPUT ROUTING:":
            self._send(c, "NAK\n\n")
            return
        changes = []
        for ln in lines[1:]:
            o, i = (int(x) for x in ln.split())
            if not (0 <= o < self.outputs and 0 <= i < self.inputs) or self.nak_next:
                self.nak_next = False
                self._send(c, "NAK\n\n")
                return
            self.routes_received.append((o + 1, i + 1))
            if self.locks[o] != "L":  # чужой замок: ACK, но маршрут не меняется
                changes.append((o, i))
        self._send(c, "ACK\n\n")
        for o, i in changes:
            self.routing[o] = i
        if changes:
            self._broadcast("VIDEO OUTPUT ROUTING:\n" + "".join(f"{o} {i}\n" for o, i in changes) + "\n")
