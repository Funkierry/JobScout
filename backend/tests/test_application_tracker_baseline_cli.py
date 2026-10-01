import subprocess
import sys
from pathlib import Path


def test_baseline_requires_explicit_model_cost_opt_in() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "application_tracker_baseline.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "--allow-model-cost" in result.stderr
