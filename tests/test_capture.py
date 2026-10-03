import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launch behavior")
@pytest.mark.parametrize("extension", [".cmd", ".bat"])
@pytest.mark.parametrize("entry", ["cli", "module"])
def test_windows_batch_producers_rejected_before_capture(tmp_path, monkeypatch, capsys, extension, entry):
    from jev_decision import capture
    from jev_decision.cli import main
    batch = tmp_path / ("producer" + extension)
    batch.write_text("@echo private-output", encoding="utf-8")
    monkeypatch.setattr(capture.shutil, "which", lambda _name: str(batch))
    monkeypatch.setattr(capture.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("batch producer launched"))
    target = tmp_path / "captured"
    invoke = main if entry == "cli" else capture.main
    assert invoke((["capture"] if entry == "cli" else []) + ["--directory", str(target), "--", "producer"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "batch_producer_requires_explicit_interpreter"
    assert "node.exe" in result["hint"] and "private-output" not in json.dumps(result)
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launch behavior")
def test_windows_explicit_batch_path_is_rejected_without_path_resolution(tmp_path, monkeypatch):
    from jev_decision import capture
    monkeypatch.setattr(capture.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="batch_producer_requires_explicit_interpreter"):
        capture.capture_output(tmp_path / "capture", [str(tmp_path / "producer.CMD")])


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launch behavior")
@pytest.mark.parametrize("suffix", [" ", ".", "  ", " ."])
@pytest.mark.parametrize("spelling", ["plain", "upper", "mixed"])
def test_windows_batch_alias_spellings_are_rejected(tmp_path, monkeypatch, suffix, spelling):
    """Trailing dots and spaces are stripped by Win32, so ``producer.cmd `` runs the batch file."""
    from jev_decision import capture
    monkeypatch.setattr(capture.shutil, "which", lambda _name: None)
    name = {"plain": "producer.cmd", "upper": "PRODUCER.CMD", "mixed": "Producer.Cmd"}[spelling]
    with pytest.raises(ValueError, match="batch_producer_requires_explicit_interpreter"):
        capture.capture_output(tmp_path / "capture", [str(tmp_path / name) + suffix])


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launch behavior")
def test_windows_non_batch_executable_is_not_mistaken_for_a_batch_script(tmp_path, monkeypatch):
    from jev_decision import capture
    monkeypatch.setattr(capture.shutil, "which", lambda _name: None)
    assert capture._windows_batch_executable(str(tmp_path / "python.exe")) is None
    assert capture._windows_batch_executable("node") is None


@pytest.mark.parametrize("entry", ["example", "cli", "module"])
def test_capture_preserves_binary_streams_exit_status_and_originals(tmp_path, entry):
    target = tmp_path / "evidence"
    producer = "import sys; sys.stdout.buffer.write('日本語 😀\\n'.encode()); sys.stderr.buffer.write(b'warning\\r\\n'); sys.exit(7)"
    launcher = {"example": [str(ROOT / "examples/capture.py")], "cli": ["-m", "jev_decision.cli", "capture"],
                "module": ["-m", "jev_decision.capture"]}[entry]
    result = subprocess.run([sys.executable, *launcher, "--directory", str(target), "--",
                             sys.executable, "-c", producer], cwd=tmp_path if entry == "example" else ROOT,
                            capture_output=True, timeout=10)
    assert result.returncode == 7
    reference = json.loads(result.stdout)
    assert reference["producer_exit_status"] == 7 and "日本語" not in result.stdout.decode()
    manifest = json.loads(Path(reference["capture"]).read_text(encoding="utf-8"))
    assert Path(manifest["streams"]["stdout"]["path"]).read_bytes() == "日本語 😀\n".encode()
    assert Path(manifest["streams"]["stderr"]["path"]).read_bytes() == b"warning\r\n"
    assert manifest["originals_user_owned"] is True


def test_capture_cli_bypasses_runtime_and_preserves_argv(tmp_path, monkeypatch, capsys):
    from jev_decision.cli import main
    from jev_decision.runtime import RuntimeConfig
    monkeypatch.setattr(RuntimeConfig, "load", classmethod(lambda cls: pytest.fail("capture loaded runtime")))
    target = tmp_path / "captured"
    marker = tmp_path / "shell expansion must not run"
    arguments = ["--flag", "two words", f"$(touch {marker})", "日本語"]
    producer = "import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(7)"
    assert main(["capture", "--directory", str(target), "--", sys.executable, "-c", producer, *arguments]) == 7
    reference = json.loads(capsys.readouterr().out)
    assert reference["producer_exit_status"] == 7 and not marker.exists()
    assert json.loads((target / "stdout.log").read_bytes()) == arguments
    original = (target / "stdout.log").read_bytes()
    assert main(["capture", "--directory", str(target), "--", sys.executable, "-c", "print('overwrite')"]) == 2
    assert (target / "stdout.log").read_bytes() == original


def test_capture_failed_launch_still_records_status_and_streams(tmp_path, capsys):
    from jev_decision.capture import capture_output
    target = tmp_path / "failed-launch"
    assert capture_output(target, [str(tmp_path / "missing-executable")]) == 127
    reference = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(reference["capture"]).read_bytes())
    assert manifest["producer_exit_status"] == 127 and manifest["error_code"] == "producer_launch_failed"
    assert all(value["bytes"] == 0 for value in manifest["streams"].values())


def test_cli_stdin_ignores_windows_pipe_locale():
    value = '{"text":"日本語 😀"}'
    result = subprocess.run([sys.executable, "-c", "import json; from jev_decision.cli import _input; print(json.dumps(_input('-')))"],
        input=value.encode(), capture_output=True, timeout=10, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert result.returncode == 0
    assert json.loads(result.stdout) == value


def test_native_capture_wrapper(tmp_path):
    producer = tmp_path / 'producer.py'
    producer.write_text("import sys; print('fixture'); print('warning', file=sys.stderr); sys.exit(7)", encoding='utf-8')
    target = tmp_path / 'wrapper-output'
    if os.name == 'nt':
        shell = shutil.which('pwsh')
        if not shell:
            pytest.skip('PowerShell unavailable')
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        script = ('& ' + quote(ROOT / 'examples/capture.ps1') + ' -Directory ' + quote(target) +
                  ' -Python ' + quote(sys.executable) + ' -Command @(' + quote(sys.executable) + ', ' + quote(producer) + '); exit $LASTEXITCODE')
        command = [shell, '-NoProfile', '-Command', script]
    else:
        command = ['sh', str(ROOT / 'examples/capture.sh'), str(target), sys.executable, str(producer)]
    result = subprocess.run(command, capture_output=True, timeout=15)
    assert result.returncode == 7, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result.stdout)['producer_exit_status'] == 7
    assert (target / 'stdout.log').read_text().strip() == 'fixture'
