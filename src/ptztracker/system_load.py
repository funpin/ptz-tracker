"""Нагрузка на компьютер: всего и доля этой программы, снимок раз в секунду в фоновом потоке.

Проценты — от всего компьютера: 12 % процессора значат 12 % всей его мощности, а не одного ядра.
Сбой источника даёт None («—» в окне), а не исключение: показ нагрузки не должен мешать работе."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

WARN_PERCENT, CRIT_PERCENT = 85.0, 95.0  # выше — оранжевым и красным
MAC_GPU_EVERY_S = 2.0  # ioreg — отдельный процесс, чаще незачем
_GIB = 1024**3

GpuReader = Callable[[], tuple[float | None, float | None]]


@dataclass(frozen=True)
class Metric:
    total: float | None = None
    own: float | None = None  # доля этой программы; None — система не сообщает
    detail: str = ""


@dataclass(frozen=True)
class LoadSnapshot:
    cpu: Metric = Metric()
    gpu: Metric = Metric()
    ram: Metric = Metric()


def load_level(value: float | None) -> str:
    if value is None or value <= WARN_PERCENT:
        return ""
    return "warn" if value <= CRIT_PERCENT else "crit"


def parse_ioreg(text: str) -> float | None:
    m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', text)
    return float(m.group(1)) if m else None


def gpu_from_engines(samples: dict[str, float], pid: int) -> tuple[float | None, float | None]:
    """Как Диспетчер задач: сумма по типу движка (3D, Compute…), берётся самый занятый тип."""
    if not samples:
        return None, None
    total: dict[str, float] = defaultdict(float)
    own: dict[str, float] = defaultdict(float)
    prefix = f"pid_{pid}_"
    for name, value in samples.items():
        m = re.search(r"engtype_(.+)$", name)
        if not m:
            continue
        total[m.group(1)] += value
        if name.startswith(prefix):
            own[m.group(1)] += value
    if not total:
        return None, None
    return min(100.0, max(total.values())), min(100.0, max(own.values(), default=0.0))


class _PdhGpu:
    """Счётчики видеокарты Windows («GPU Engine»), те же, что у Диспетчера задач."""

    _COUNTER = r"\GPU Engine(*)\Utilization Percentage"
    _PDH_FMT_DOUBLE = 0x200
    _PDH_MORE_DATA = 0x800007D2 - (1 << 32)

    def __init__(self) -> None:
        import ctypes

        self._ct = ctypes
        self._pdh = ctypes.windll.pdh
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)):
            raise OSError("PdhOpenQueryW")
        if self._pdh.PdhAddEnglishCounterW(self._query, self._COUNTER, 0, ctypes.byref(self._counter)):
            raise OSError("PdhAddEnglishCounterW")
        self._pdh.PdhCollectQueryData(self._query)

    def __call__(self) -> tuple[float | None, float | None]:
        ct = self._ct

        class _Value(ct.Structure):
            _fields_ = [("CStatus", ct.c_ulong), ("doubleValue", ct.c_double)]

        class _Item(ct.Structure):
            _fields_ = [("szName", ct.c_wchar_p), ("FmtValue", _Value)]

        if self._pdh.PdhCollectQueryData(self._query):
            return None, None
        size, count = ct.c_ulong(0), ct.c_ulong(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            self._counter, self._PDH_FMT_DOUBLE, ct.byref(size), ct.byref(count), None)
        if status != self._PDH_MORE_DATA or not size.value:
            return None, None
        buf = ct.create_string_buffer(size.value)
        if self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, self._PDH_FMT_DOUBLE, ct.byref(size), ct.byref(count), buf):
            return None, None
        items = ct.cast(buf, ct.POINTER(_Item))
        samples = {items[i].szName: items[i].FmtValue.doubleValue for i in range(count.value)
                   if items[i].FmtValue.CStatus == 0}
        return gpu_from_engines(samples, os.getpid())


class _MacGpu:
    """Общая загрузка видеокарты из ioreg; долю процесса без прав администратора не узнать."""

    def __init__(self) -> None:
        self._next = 0.0
        self._last: float | None = None

    def __call__(self) -> tuple[float | None, float | None]:
        now = time.monotonic()
        if now >= self._next:
            self._next = now + MAC_GPU_EVERY_S
            out = subprocess.run(["ioreg", "-r", "-c", "IOAccelerator", "-d", "1"],
                                 capture_output=True, text=True, timeout=1.0)
            self._last = parse_ioreg(out.stdout)
        return self._last, None


def _default_gpu_reader() -> GpuReader:
    try:
        if sys.platform == "win32":
            return _PdhGpu()
        if sys.platform == "darwin":
            return _MacGpu()
    except Exception:
        log.exception("Нагрузка видеокарты недоступна")
    return lambda: (None, None)


def _gb(n: float) -> str:
    return f"{n / _GIB:.1f}".replace(".", ",")


class SystemLoad:
    def __init__(self, interval_s: float = 1.0, gpu_reader: GpuReader | None = None):
        self.interval_s = interval_s
        self._gpu = gpu_reader
        self._proc = psutil.Process()
        self._cores = psutil.cpu_count(logical=True) or 1
        self._lock = threading.Lock()
        self._snap = LoadSnapshot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="system-load", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> LoadSnapshot:
        with self._lock:
            return self._snap

    def sample_once(self) -> LoadSnapshot:
        cores = psutil.cpu_count(logical=True) or self._cores
        cpu = Metric(psutil.cpu_percent(None), min(100.0, self._proc.cpu_percent(None) / cores),
                     f"{cores} потоков")
        vm = psutil.virtual_memory()
        rss = self._proc.memory_info().rss
        ram = Metric(vm.percent, min(100.0, rss / vm.total * 100),
                     f"{_gb(vm.used)} из {_gb(vm.total)} ГБ · PTZ {_gb(rss)} ГБ")
        return LoadSnapshot(cpu, self._read_gpu(), ram)

    def _read_gpu(self) -> Metric:
        if self._gpu is None:
            self._gpu = _default_gpu_reader()  # в фоновом потоке: PDH открывается не мгновенно
        try:
            total, own = self._gpu()
        except Exception:
            log.debug("Не удалось прочитать нагрузку видеокарты", exc_info=True)
            return Metric()
        return Metric(total, own)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snap = self.sample_once()
            except Exception:
                log.exception("Снимок нагрузки не удался")
                snap = LoadSnapshot()
            with self._lock:
                self._snap = snap
            self._stop.wait(self.interval_s)
