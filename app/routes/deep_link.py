"""Short links from an outside system back into a Hub result.

EcoTaxa stores a per-object URL in ``object_link``, a ``VARCHAR(255)``. A
link that spelled out the HDF5 path and the frame name in query parameters
came to 242 characters — inside the limit, but with so little headroom that
a deeper workflow path or a longer station name would silently overflow.

So the link names the workflow instead of the file, and this resolves it.
That also survives results being moved: the path is looked up at click time
rather than baked into thousands of database rows.
"""

import os
from flask import Blueprint, current_app, redirect, url_for, abort
from flask_login import login_required

from ..backend.models import Workflow, Task, Artifact
from ..backend.artifacts import data_root

deep_link_bp = Blueprint('deep_link', __name__)

# The extraction pass writes this beside the correction checkpoints.
RESULT_FILE = "result.h5"


def _find_result_file(workflow_id):
    """The result.h5 a workflow produced, or None.

    Prefers what the tasks actually declared as outputs; falls back to the
    Hub's own layout for workflows whose artifacts were never recorded.
    """
    artifacts = (Artifact.query
                 .join(Task, Artifact.task_id == Task.id)
                 .filter(Task.workflow_id == workflow_id)
                 .order_by(Task.workflow_position)
                 .all())
    for artifact in artifacts:
        candidate = os.path.join(artifact.path, RESULT_FILE)
        if os.path.isfile(candidate):
            return candidate

    workflow_dir = os.path.join(data_root(current_app), "workflows",
                                str(workflow_id))
    if os.path.isdir(workflow_dir):
        for entry in sorted(os.listdir(workflow_dir)):
            candidate = os.path.join(workflow_dir, entry, "results", RESULT_FILE)
            if os.path.isfile(candidate):
                return candidate
    return None


@deep_link_bp.route('/<workflow_id>/<path:frame>/<int:index>')
@login_required
def object_in_frame(workflow_id, frame, index):
    """Open the HDF profile viewer on one object of one frame.

    ``index`` is the object number as it appears in the EcoTaxa object_id,
    which counts from 1; the viewer does the conversion.
    """
    if Workflow.query.get(workflow_id) is None:
        abort(404, description=f"No workflow {workflow_id}")

    result_file = _find_result_file(workflow_id)
    if result_file is None:
        abort(404, description=(
            f"Workflow {workflow_id} has no {RESULT_FILE}. It may predate the "
            "extraction pass, or its results may have been removed."))

    return redirect(url_for('hdf-profile-viewer.index',
                            path=result_file, group=frame, index=index))
