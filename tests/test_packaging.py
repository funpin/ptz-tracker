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


def operator_guide() -> str:
    return (ROOT / "docs" / "OPERATOR.md").read_text(encoding="utf-8")


def test_readme_links_operator_guide_and_license():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/OPERATOR.md" in text and "AGPL" in text


def test_guide_has_diagnostics_section():
    text = operator_guide()
    assert "Диагностика камеры" in text and "PTZTracker-diag.exe" in text


def test_spec_bundles_model_and_onnxruntime():
    text = (ROOT / "packaging" / "ptztracker.spec").read_text(encoding="utf-8")
    assert "yolo11n-pose.onnx" in text and '"onnxruntime"' in text


def test_guide_has_target_selection():
    text = operator_guide()
    assert "Кликните по докладчику" in text and "ЦЕЛЬ ПОТЕРЯНА" in text


def test_guide_has_following_calibration_and_f9():
    text = operator_guide()
    assert "## 3. Слежение" in text and "Калибровка" in text and "пульт" in text.lower() and "F9" in text


def test_version_has_changelog_section_and_release_workflow():
    from ptztracker import __version__

    assert f"## [{__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "windows-2022" in wf and "macos-14" in wf and "gh release create" in wf


def test_mac_bundle_is_a_regular_app():
    # иначе macOS считает приложение фоновым: нет своего меню и значка в Dock
    text = (ROOT / "packaging" / "ptztracker.spec").read_text(encoding="utf-8")
    assert '"LSBackgroundOnly": False' in text and "NSLocalNetworkUsageDescription" in text
