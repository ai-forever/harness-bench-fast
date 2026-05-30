from __future__ import annotations

import tarfile

from harness_bench.__main__ import main
from harness_bench.harbor_export import export_harbor_dataset
from harness_bench.tasks import get_task


def test_apply_gold_and_verify_task_cli_without_docker(tmp_path):
    task = get_task("task_06_toggle_debug")
    task.setup(tmp_path)

    assert main(["verify-task", "--task", task.id, "--workspace", str(tmp_path)]) == 1
    assert main(["apply-gold", "--task", task.id, "--workspace", str(tmp_path)]) == 0
    assert main(["verify-task", "--task", task.id, "--workspace", str(tmp_path)]) == 0


def test_export_harbor_dataset_materializes_task(tmp_path):
    output = tmp_path / "harbor_dataset"
    result = export_harbor_dataset(
        output,
        task_ids=["task_06_toggle_debug"],
        clean=True,
    )

    task_dir = output / "task_06_toggle_debug"
    assert result.task_count == 1
    assert result.task_ids == ("task_06_toggle_debug",)
    assert (output / "dataset.toml").exists()
    assert (task_dir / "task.toml").exists()
    assert (task_dir / "instruction.md").exists()
    assert (task_dir / "environment" / "Dockerfile").exists()
    assert (task_dir / "solution" / "solve.sh").exists()
    assert (task_dir / "solution" / "benchmark" / "harness_bench" / "__main__.py").exists()
    assert (task_dir / "tests" / "test.sh").exists()
    assert (task_dir / "tests" / "benchmark" / "harness_bench" / "__main__.py").exists()

    task_toml = (task_dir / "task.toml").read_text()
    assert 'name = "ai-forever/harness-bench-fast__task_06_toggle_debug"' in task_toml
    assert 'task_set_version = "0.3.0"' in task_toml

    with tarfile.open(task_dir / "environment" / "setup.tar") as archive:
        assert "config.py" in archive.getnames()

    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert "/opt/benchmark" not in dockerfile

    test_sh = (task_dir / "tests" / "test.sh").read_text()
    assert "/tests/benchmark" in test_sh
