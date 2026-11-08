"""Снимок нагрузки на компьютер: разбор ответов систем, доля программы, устойчивость к сбоям."""

import sys
import time

import pytest

from ptztracker import system_load as sl
from ptztracker.system_load import SystemLoad, gpu_from_engines, load_level, parse_ioreg


def wait_for(cond, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_levels():
    assert [load_level(v) for v in (None, 85, 85.1, 95, 95.1)] == ["", "", "warn", "warn", "crit"]


def test_ioreg():
    assert parse_ioreg('"PerformanceStatistics" = {"Device Utilization %"=64,"x"=1}') == 64.0
    assert parse_ioreg("") is None


def test_engines_busiest_type_and_own_pid():
    s = {
        "pid_10_luid_0x0_phys_0_eng_0_engtype_3D": 30.0,
        "pid_20_luid_0x0_phys_0_eng_0_engtype_3D": 25.0,
        "pid_10_luid_0x0_phys_0_eng_1_engtype_Compute": 10.0,
    }
    assert gpu_from_engines(s, 10) == (55.0, 30.0)


def test_engines_cap_and_empty():
    assert gpu_from_engines({"pid_1_x_engtype_3D": 150.0}, 1) == (100.0, 100.0)
    assert gpu_from_engines({}, 1) == (None, None)


def test_snapshot_survives_gpu_failure():
    def boom():
        raise RuntimeError("нет видеокарты")

    s = SystemLoad(interval_s=0.05, gpu_reader=boom)
    s.start()
    try:
        assert wait_for(lambda: s.snapshot().cpu.total is not None)
        assert s.snapshot().gpu.total is None
    finally:
        s.stop()


def test_own_cpu_is_share_of_whole_computer(monkeypatch):
    monkeypatch.setattr(sl.psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(sl.psutil.Process, "cpu_percent", lambda self, interval=None: 400.0)
    s = SystemLoad(interval_s=0.05, gpu_reader=lambda: (None, None))
    s.start()
    try:
        assert wait_for(lambda: s.snapshot().cpu.own == 50.0)
    finally:
        s.stop()


def test_ram_detail_uses_comma_and_gb():
    s = SystemLoad(gpu_reader=lambda: (None, None))
    snap = s.sample_once()
    assert snap.ram.total is not None and snap.ram.own is not None
    assert " ГБ" in snap.ram.detail and "PTZ" in snap.ram.detail


@pytest.mark.skipif(sys.platform != "win32", reason="счётчики PDH есть только на Windows")
def test_real_pdh_reads_without_error():
    gpu = sl._PdhGpu()
    gpu()
    time.sleep(0.2)
    total, own = gpu()
    assert total is None or 0 <= total <= 100
    assert own is None or 0 <= own <= 100
