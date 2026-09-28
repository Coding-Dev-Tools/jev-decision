"""Build and install release candidates outside the checkout, without publishing."""
import argparse
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = '''import asyncio, json, sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters
async def main():
    for mode in ('legacy', '2026-07-28'):
        async with Client(StdioServerParameters(command=sys.executable, args=['-I', '-m', 'jev_decision.mcp'],
                                               env={'JEV_HOME':sys.argv[1]}), mode=mode) as client:
            assert len((await client.list_tools()).tools) == 6
            status = (await client.call_tool('jev_status', {})).structured_content
            assert status['enabled'] is False and status['authenticated'] is False
    print(json.dumps({'protocols':['legacy','2026-07-28'],'provider_calls':0}))
asyncio.run(main())
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    outside = output / "outside"
    outside.mkdir()
    env = {key: value for key, value in os.environ.items() if key not in
           {"PYTHONPATH", "TYPESAFE_API_KEY", "JEV_API_KEY", "JEV_OFFLINE_MODE", "JEV_ENDPOINT_URL"}}
    env["JEV_HOME"] = str(output / "state")

    def run(command, cwd=outside):
        subprocess.run([str(item) for item in command], cwd=cwd, env=env, check=True, timeout=240)

    run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", output / "dist", ROOT])
    wheel = next((output / "dist").glob("*.whl"))
    source = next((output / "dist").glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        assert any(name.endswith("/LICENSE") for name in archive.namelist())
        assert "jev_decision/resources/jev-skill.md" in archive.namelist()
    with tarfile.open(source) as archive:
        assert any(name.endswith("/LICENSE") for name in archive.getnames())
        assert any(name.endswith("/examples/capture.py") for name in archive.getnames())
    for label, artifact in (("wheel", wheel), ("sdist", source)):
        environment = output / label
        run([sys.executable, "-m", "venv", environment])
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([python, "-m", "pip", "install", "--no-cache-dir", artifact])
        run([python, "-I", "-c", "import sys, jev_decision, jev_decision.cli; assert jev_decision.__version__ == '0.3.0'; assert 'mcp' not in sys.modules"])
        run([python, "-I", "-m", "jev_decision.cli", "doctor", "--json"])
        run([python, "-m", "pip", "install", str(artifact) + "[mcp]"])
        script = outside / (label + "_mcp.py")
        script.write_text(SMOKE, encoding="utf-8")
        run([python, "-I", script, env["JEV_HOME"]])
    (output / "verification.json").write_text(json.dumps({"version": "0.3.0", "platform": sys.platform,
        "python": sys.version.split()[0], "wheel": wheel.name, "source": source.name,
        "clean_installs": ["wheel", "sdist"], "protocols": ["legacy", "2026-07-28"],
        "provider_calls": 0, "published": False}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
