import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_capture_preserves_binary_streams_exit_status_and_originals(tmp_path):
    target = tmp_path / "evidence"
    producer = "import sys; sys.stdout.buffer.write('日本語 😀\\n'.encode()); sys.stderr.buffer.write(b'warning\\r\\n'); sys.exit(7)"
    result = subprocess.run([sys.executable, str(ROOT / "examples/capture.py"), "--directory", str(target), "--",
                             sys.executable, "-c", producer], cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode == 7
    reference = json.loads(result.stdout)
    assert reference["producer_exit_status"] == 7 and "日本語" not in result.stdout.decode()
    manifest = json.loads(Path(reference["capture"]).read_text(encoding="utf-8"))
    assert Path(manifest["streams"]["stdout"]["path"]).read_bytes() == "日本語 😀\n".encode()
    assert Path(manifest["streams"]["stderr"]["path"]).read_bytes() == b"warning\r\n"
    assert manifest["originals_user_owned"] is True


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
