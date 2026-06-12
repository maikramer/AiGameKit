"""In-process rigging: extract + skeleton + skin without subprocess launches.

Eliminates 4 subprocess launches (extract.sh + run.py x 2 stages) by calling
``extract_builtin`` and ``run_task`` directly, saving ~30-50s of import overhead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_unirig_env(root: Path) -> None:
    """Ensure PYTHONPATH includes root so ``from src.*`` imports work."""
    root_s = str(root)
    pp = os.environ.get("PYTHONPATH", "")
    if root_s not in pp.split(os.pathsep):
        os.environ["PYTHONPATH"] = root_s if not pp else root_s + os.pathsep + pp


def run_skeleton_inprocess(
    root: Path,
    *,
    input_path: str | None = None,
    input_dir: str | None = None,
    output_path: str | None = None,
    output_dir: str | None = None,
    seed: int = 123,
    skeleton_task: str = "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml",
    npz_dir: str = "tmp",
    faces_target_count: int = 50000,
) -> None:
    """Run extract + skeleton inference in the current process."""
    _setup_unirig_env(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    abs_npz_dir = os.path.abspath(npz_dir)

    from src.data.extract import extract_builtin, get_files

    files = get_files(
        data_name="raw_data.npz",
        inputs=input_path,
        input_dataset_dir=input_dir or "",
        output_dataset_dir=abs_npz_dir,
        require_suffix=["obj", "fbx", "FBX", "dae", "glb", "gltf", "vrm"],
        force_override=True,
        warning=False,
    )
    if files:
        extract_builtin(
            output_folder=abs_npz_dir,
            target_count=faces_target_count,
            num_runs=1,
            id=0,
            time="_oneshot_",
            files=files,
        )

    from rigging3d.unirig.run import run_task

    orig_cwd = os.getcwd()
    try:
        os.chdir(str(root))
        run_task(
            skeleton_task,
            seed=seed,
            input=input_path,
            input_dir=input_dir,
            output=output_path,
            output_dir=output_dir,
            npz_dir=abs_npz_dir,
        )
    finally:
        os.chdir(orig_cwd)


def run_skin_inprocess(
    root: Path,
    *,
    input_path: str | None = None,
    input_dir: str | None = None,
    output_path: str | None = None,
    output_dir: str | None = None,
    seed: int = 123,
    skin_task: str = "configs/task/quick_inference_unirig_skin.yaml",
    npz_dir: str = "tmp",
    data_name: str = "raw_data.npz",
    faces_target_count: int = 50000,
) -> None:
    """Run extract + skin inference in the current process."""
    _setup_unirig_env(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    abs_npz_dir = os.path.abspath(npz_dir)

    from src.data.extract import extract_builtin, get_files

    files = get_files(
        data_name=data_name,
        inputs=input_path,
        input_dataset_dir=input_dir or "",
        output_dataset_dir=abs_npz_dir,
        require_suffix=["obj", "fbx", "FBX", "dae", "glb", "gltf", "vrm"],
        force_override=True,
        warning=False,
    )
    if files:
        extract_builtin(
            output_folder=abs_npz_dir,
            target_count=faces_target_count,
            num_runs=1,
            id=0,
            time="_oneshot_",
            files=files,
        )

    from rigging3d.unirig.run import run_task

    orig_cwd = os.getcwd()
    try:
        os.chdir(str(root))
        run_task(
            skin_task,
            seed=seed,
            input=input_path,
            input_dir=input_dir,
            output=output_path,
            output_dir=output_dir,
            npz_dir=abs_npz_dir,
            cls=None,
            data_name_override=data_name,
        )
    finally:
        os.chdir(orig_cwd)
