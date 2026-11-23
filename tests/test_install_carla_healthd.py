from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_healthd_installer_stages_helpers_and_backs_up_existing_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    target = home / "bin"
    target.mkdir(parents=True)
    (target / "roaring_carla_healthd.py").write_text("# old version\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HEALTHD_INSTALL_TARGET"] = str(target)
    result = subprocess.run(
        ["bash", str(repo_root / "scripts" / "install_carla_healthd.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = {
        "roaring_carla_healthd.py",
        "audio_health_baseline.py",
        "audio_health_signal.py",
        "audio_health_stream.py",
        "audio_health_transport.py",
    }
    assert expected.issubset({p.name for p in target.iterdir()})
    assert (target / "roaring_carla_healthd.py").stat().st_mode & 0o111
    backups = list(
        (home / ".local/share/roaring/carla-healthd-backups").glob("*/roaring_carla_healthd.py")
    )
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "# old version\n"
    assert "No systemd unit changed or restarted" in result.stdout
