"""Profiles the Hub processed itself, as opposed to the old pipeline's tree.

The old route left everything a concentration needs beside the TSV: PNGs
whose names carry the pressure, and settings.csv with the mask radius. A Hub
export sits in its workflow's output directory with neither, so the frames
are found through the workflow instead: the profile_dataset.h5 it read
(every frame, with its pressure), or failing that the result.h5 it wrote.

result.h5 is only a fallback: the segmenter writes a group per frame that
kept objects, so empty frames are missing and the sampled volume comes out
too small. The load status says so.
"""
import json
import os
import re
from pathlib import Path

import h5py
import numpy as np

from app.backend.models import Artifact, Task, Workflow

DATASET_FILE = "profile_dataset.h5"
RESULT_FILE = "result.h5"
EXPORT_TOOL = "pisco_ecotaxa_export"

_WORKFLOW_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# Pressure in a frame name: 0009.94dbar, or bar on some earlier cruises.
_PRESSURE = re.compile(r"_(\d+\.\d+)(d?bar)")


def workflow_for_path(path: Path):
    """The workflow whose output directory holds path, or None.

    Hub outputs live under <DATA_ROOT>/workflows/<workflow-id>/…; the id is
    the last one in the path, so a workflow nested in another's tree (it
    happens with copied results) resolves to the inner one.
    """
    for part in reversed(Path(path).parts):
        if _WORKFLOW_ID.fullmatch(part):
            workflow = Workflow.query.get(part)
            if workflow is not None:
                return workflow
    return None


def _strings(node):
    """Every string in parsed settings or params, however nested."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _candidate_paths(workflow):
    """Paths the workflow wrote or read: its artifacts, then any string in
    its tasks' settings or its params that looks like a path."""
    seen = []
    artifacts = (Artifact.query
                 .join(Task, Artifact.task_id == Task.id)
                 .filter(Task.workflow_id == workflow.id)
                 .order_by(Task.workflow_position)
                 .all())
    seen.extend(a.path for a in artifacts)

    sources = []
    for task in sorted(workflow.tasks, key=lambda t: t.workflow_position):
        try:
            sources.append(json.loads(task.settings or "{}"))
        except ValueError:
            continue  # opaque settings
    try:
        sources.append(json.loads(workflow.params or "{}"))
    except ValueError:
        pass
    for source in sources:
        seen.extend(s for s in _strings(source) if s.startswith("/"))
    return list(dict.fromkeys(seen))


def _find(workflow, filename):
    for path in _candidate_paths(workflow):
        candidate = path if os.path.basename(path) == filename else os.path.join(path, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def _dataset_pressures(path):
    with h5py.File(path, "r") as f:
        if "frame/pressure_dbar" in f:
            return np.asarray(f["frame/pressure_dbar"][()], dtype=float)
        names = [n.decode() if isinstance(n, bytes) else n for n in f["names"][()]]
    return _name_pressures(names)


def _name_pressures(names):
    out = []
    for name in names:
        m = _PRESSURE.search(name)
        if m:
            out.append(float(m.group(1)) * (1.0 if m.group(2) == "dbar" else 10.0))
    return np.asarray(out, dtype=float)


def _result_pressures(path):
    with h5py.File(path, "r") as f:
        return _name_pressures(list(f.keys()))


def frame_pressures(tsv_path: Path):
    """(pressures in dbar, one per frame; where they came from; warning or
    None) for a TSV in a Hub workflow's output, or None if there is no
    workflow or no frame list to be found."""
    workflow = workflow_for_path(tsv_path)
    if workflow is None:
        return None

    dataset = _find(workflow, DATASET_FILE)
    if dataset:
        pressures = _dataset_pressures(dataset)
        pressures = pressures[np.isfinite(pressures)]
        if len(pressures):
            return pressures, f"{DATASET_FILE} of workflow '{workflow.name}'", None

    result = _find(workflow, RESULT_FILE)
    if result:
        pressures = _result_pressures(result)
        if len(pressures):
            return (pressures, f"{RESULT_FILE} of workflow '{workflow.name}'",
                    "frames without objects are not in result.h5, so the sampled "
                    "volume is too small and concentrations too high")
    return None


def hub_exports():
    """Workflows that ran the EcoTaxa export and whose TSV is still there,
    newest first: what the explorer offers without browsing for a file."""
    tasks = (Task.query.join(Workflow, Task.workflow_id == Workflow.id)
             .filter(Task.status == "Finished")
             .order_by(Workflow.created_at.desc())
             .all())
    out = []
    for task in tasks:
        if task.tool is None or task.tool.name != EXPORT_TOOL:
            continue
        for artifact in task.artifacts:
            directory = Path(artifact.path)
            if not directory.is_dir():
                continue
            for tsv in sorted(directory.glob("ecotaxa_*.tsv")):
                out.append({
                    "workflow_id": task.workflow_id,
                    "workflow": task.workflow.name or task.workflow_id,
                    "created": (task.workflow.created_at.isoformat(timespec="minutes")
                                if task.workflow.created_at else None),
                    "path": str(tsv),
                })
    return out
