from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_spec_compiles_and_names_both_exes():
    text = (ROOT / "packaging" / "ptztracker.spec").read_text(encoding="utf-8")
    compile(text, "ptztracker.spec", "exec")
    assert '"PTZTracker"' in text and '"PTZTracker-diag"' in text


def test_launchers_exist():
    assert "main()" in (ROOT / "packaging" / "launch_gui.py").read_text(encoding="utf-8")
    assert "diag_main()" in (ROOT / "packaging" / "launch_diag.py").read_text(encoding="utf-8")


def test_build_bat():
    text = (ROOT / "build.bat").read_text(encoding="utf-8")
    assert "py -3.12" in text and "pyinstaller" in text.lower()


def test_ci_workflow():
    text = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    assert "jobs:" in text and "windows-2022" in text and "pyinstaller" in text


def test_readme_has_diagnostics_section():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Диагностика камеры на площадке" in text and "PTZTracker-diag.exe" in text
