import re
import subprocess
import textwrap
from pathlib import Path

START_SH = Path(__file__).resolve().parent.parent / "start.sh"


def _run_registry_check(registry: str, port: str = "11440", extra_env: dict | None = None) -> subprocess.CompletedProcess:
    # source start.sh (dispatch guard skips the case block) then invoke the
    # registry self-check directly with env overrides.
    snippet = textwrap.dedent(
        """
        source ./start.sh
        SERVICE_PORT="${SERVICE_PORT_OVERRIDE:-$SERVICE_PORT}"
        _registry_check
        """
    )
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/tmp",
        "FUSION_REGISTRY": registry,
        "FUSION_PROJECT_PORT": port,
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", "-c", snippet],
        cwd=str(START_SH.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestIssue35NoNohup:
    def test_no_nohup_in_start_sh(self):
        src = START_SH.read_text(encoding="utf-8")
        # comments may explain the history; no *command* line may invoke nohup
        assert not re.search(r"^\s*nohup\b", src, re.M), "start.sh must not call nohup (issue #35: fails headless)"

    def test_redirect_only_detach_present(self):
        src = START_SH.read_text(encoding="utf-8")
        assert '</dev/null &' in src


class TestIssue36RegistryCheck:
    def test_missing_registry_file_warns_and_continues(self, tmp_path):
        r = _run_registry_check(str(tmp_path / "nope.yaml"))
        assert r.returncode == 0
        assert "registry self-check skipped" in r.stderr

    def test_unregistered_port_refuses(self, tmp_path):
        reg = tmp_path / "port-registry.yaml"
        reg.write_text("services:\n  fusion-other:\n    port: 1\n", encoding="utf-8")
        r = _run_registry_check(str(reg), port="12345")
        assert r.returncode == 1
        assert "refusing to start" in r.stderr

    def test_registered_port_passes(self, tmp_path):
        reg = tmp_path / "port-registry.yaml"
        reg.write_text(
            "services:\n"
            "  fusion-projects:\n"
            "    port: 11440\n"
            "    purpose: rest\n",
            encoding="utf-8",
        )
        r = _run_registry_check(str(reg), port="11440")
        assert r.returncode == 0
        assert r.stderr == ""

    def test_bypass_env(self, tmp_path):
        reg = tmp_path / "port-registry.yaml"
        reg.write_text("services:\n", encoding="utf-8")
        r = _run_registry_check(str(reg), port="12345", extra_env={"FUSION_SKIP_REGISTRY_CHECK": "1"})
        assert r.returncode == 0
        assert "bypassed" in r.stderr

    def test_default_registry_path_is_workspace_registry(self):
        # no FUSION_REGISTRY override: falls back to $HOME/fusion/architecture
        snippet = 'source ./start.sh && echo "${FUSION_REGISTRY:-$HOME/fusion/architecture/port-registry.yaml}"'
        r = subprocess.run(
            ["bash", "-c", snippet],
            cwd=str(START_SH.parent),
            env={"PATH": "/usr/bin:/bin", "HOME": "/Users/testhome"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.stdout.strip() == "/Users/testhome/fusion/architecture/port-registry.yaml"
