"""Building workflows in the browser.

Two ways in, one draft underneath. The guided path picks a template,
asks for its parameters and shows a review; the free path is the
builder, where tools are added one by one. Both edit a TemporaryWorkflow
and both start the run through create_new_workflow, so a workflow built
here is validated exactly like one submitted through the API.
"""

import json

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from ..backend import artifacts, settings_engine, templates
from ..backend.models import (TemplateTask, TemplateWorkflow, TemporaryTask,
                              TemporaryWorkflow, Tool, Workflow, db)
from ..backend.task_manager import create_new_workflow
from ..utils import clean_string

workflow_bp = Blueprint('workflow', __name__)


# ---- helpers ------------------------------------------------------------------

def _coerce_field(prop, raw):
    """Convert a form string into the type the schema property declares."""
    prop_type = prop.get("type")
    if raw is None or raw == "":
        return None
    if prop_type == "integer":
        return int(raw)
    if prop_type == "number":
        return float(raw)
    if prop_type == "array":
        items = [s.strip() for s in raw.split(",") if s.strip()]
        item_type = prop.get("items", {}).get("type")
        if item_type == "integer":
            return [int(x) for x in items]
        if item_type == "number":
            return [float(x) for x in items]
        return items
    return raw


def _settings_from_form(schema, form):
    """Rebuild the settings object from schema-form fields (f_<key> /
    f_<section>__<key>). Checkboxes only submit when checked, so booleans
    are derived from presence."""
    obj = {}
    for key, prop in schema.get("properties", {}).items():
        if prop.get("type") == "object":
            section = {}
            for inner_key, inner_prop in prop.get("properties", {}).items():
                field = f"f_{key}__{inner_key}"
                if inner_prop.get("type") == "boolean":
                    section[inner_key] = field in form
                else:
                    value = _coerce_field(inner_prop, form.get(field))
                    if value is not None:
                        section[inner_key] = value
            obj[key] = section
        elif prop.get("type") == "boolean":
            obj[key] = f"f_{key}" in form
        else:
            value = _coerce_field(prop, form.get(f"f_{key}"))
            if value is not None:
                obj[key] = value
    return obj


def _params_from_form(schema, form):
    """Workflow parameters from the wizard form: strings as typed, which
    validate_params coerces; unchecked booleans are false; blanks are
    left out so a default or a 'required' error applies."""
    params = {}
    for key, prop in (schema or {}).get("properties", {}).items():
        if prop.get("type") == "boolean":
            params[key] = f"p_{key}" in form
            continue
        raw = form.get(f"p_{key}")
        if raw is not None and raw.strip() != "":
            params[key] = raw.strip()
    return params


def _json_or(text, default):
    try:
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def _draft_params(draft):
    return _json_or(draft.params, {})


def _task_settings_obj(task):
    """A draft task's settings as an object (structured tools) or text."""
    if (task.tool.settings_format or "opaque") == "opaque":
        return task.settings
    return _json_or(task.settings, task.settings)


def _draft_specs(draft):
    return [{"tool": t.tool.name, "settings": _task_settings_obj(t),
             "priority": t.priority, "weight": t.weight}
            for t in draft.tasks]


def _has_refs(settings_text):
    return any(key in (settings_text or "") for key in settings_engine.REF_KEYS)


def _preview(draft, task):
    """What the tool will be given, as far as it is known before the run:
    params substituted, $from shown as a pointer to the step it consumes."""
    obj = _task_settings_obj(task)
    if not isinstance(obj, dict):
        return obj or ""
    try:
        obj = settings_engine.resolve_params(obj, _draft_params(draft))
    except ValueError as e:
        return f"cannot resolve parameters: {e}"

    def show(value):
        if settings_engine.ref_kind(value) == settings_engine.FROM_KEY:
            ref = value[settings_engine.FROM_KEY]
            target = ref.get("task")
            if target == "prev":
                target = task.workflow_position - 1
            return f"← step {target} · {ref.get('output')}"
        if isinstance(value, dict):
            return {k: show(v) for k, v in value.items()}
        if isinstance(value, list):
            return [show(v) for v in value]
        return value

    return json.dumps(show(obj), indent=2, ensure_ascii=False)


def _fill_from_template(draft, template):
    """Replace a draft's steps with a template's."""
    for old in list(draft.tasks):
        db.session.delete(old)
    for tpl_task in template.tasks:
        task = TemporaryTask()
        task.workflow = draft
        task.tool_id = tpl_task.tool_id
        task.workflow_position = tpl_task.workflow_position
        task.priority = tpl_task.priority
        task.weight = tpl_task.weight
        task.settings = tpl_task.settings
        db.session.add(task)
    draft.template_id = template.id
    draft.template_name = template.name


def _prefill(template, draft=None):
    """Parameter values to show in the form: the draft being edited,
    else a run to repeat (?from=), else the sticky values of the last
    run made from this template, on top of the schema defaults."""
    schema = _json_or(template.params_schema, {}) or {}
    values = {}
    for key, prop in schema.get("properties", {}).items():
        if "default" in prop:
            values[key] = prop["default"]

    if draft is not None:
        values.update(_draft_params(draft))
        return values

    source = request.args.get("from")
    if source:
        workflow = Workflow.query.get(source)
        if workflow is not None:
            values.update(_json_or(workflow.params, {}))
            return values

    last = (Workflow.query.filter_by(template_id=template.id)
            .order_by(Workflow.created_at.desc()).first())
    if last is not None:
        previous = _json_or(last.params, {})
        for key, prop in schema.get("properties", {}).items():
            if prop.get("sticky") and key in previous:
                values[key] = previous[key]
    return values


# ---- the guided path -------------------------------------------------------------

@workflow_bp.route('/new')
@login_required
def new_workflow():
    """Step 1: what to run."""
    rows = TemplateWorkflow.query.order_by(TemplateWorkflow.name).all()
    shipped = [t for t in rows if t.provider_id is not None]
    saved = [t for t in rows if t.provider_id is None]
    return render_template('wizard_pick.html', shipped=shipped, saved=saved,
                           schemas={t.id: _json_or(t.params_schema, {}) or {}
                                    for t in rows})


@workflow_bp.route('/new/<template_id>', methods=['GET', 'POST'])
@login_required
def new_from_template(template_id):
    """Step 2: the template's parameters. Submitting makes (or updates)
    a draft and moves on to the review."""
    template = templates.find(template_id)
    if template is None:
        abort(404)
    schema = _json_or(template.params_schema, {}) or {}
    draft = None
    if request.args.get("draft"):
        draft = TemporaryWorkflow.query.get(request.args["draft"])

    if request.method == 'POST':
        params = _params_from_form(schema, request.form)
        name = request.form.get("workflow_name", "").strip() or None
        try:
            name, params, _ = templates.instantiate(template, params, name)
        except templates.TemplateError as e:
            errors = str(e).removeprefix("Invalid parameters: ").split("; ")
            return render_template('wizard_params.html', template=template,
                                   schema=schema, values=params, draft=draft,
                                   name=request.form.get("workflow_name", ""),
                                   errors=errors)
        if draft is None:
            draft = TemporaryWorkflow()
            draft.user_id = current_user.id
            db.session.add(draft)
            _fill_from_template(draft, template)
        draft.name = name
        draft.params = json.dumps(params)
        db.session.commit()
        return redirect(url_for('workflow.review', workflow_id=draft.id))

    return render_template('wizard_params.html', template=template,
                           schema=schema, values=_prefill(template, draft),
                           draft=draft, name=draft.name if draft else "",
                           errors=None)


@workflow_bp.route('/review/<workflow_id>')
@login_required
def review(workflow_id):
    """Step 3: the draft as it will run."""
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    template = templates.find(draft.template_id) if draft.template_id else None
    previews = {task.id: _preview(draft, task) for task in draft.tasks}
    return render_template('wizard_review.html', workflow=draft,
                           template=template, params=_draft_params(draft),
                           previews=previews)


@workflow_bp.route('/draft/<workflow_id>/discard', methods=['POST'])
@login_required
def discard_draft(workflow_id):
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    db.session.delete(draft)
    db.session.commit()
    return redirect(url_for('processing.dashboard'))


# ---- the builder ---------------------------------------------------------------

@workflow_bp.route('/workflow/init', methods=["POST"])
@login_required
def init_workflow():
    """Start from scratch: an empty draft."""
    draft = TemporaryWorkflow()
    draft.user_id = current_user.id
    db.session.add(draft)
    db.session.commit()
    return redirect(url_for("workflow.builder", workflow_id=draft.id))


@workflow_bp.route('/builder/<workflow_id>')
@login_required
def builder(workflow_id):
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    all_tools = Tool.query.filter(Tool.available != False).order_by(Tool.name).all()
    all_templates = TemplateWorkflow.query.order_by(TemplateWorkflow.name).all()
    return render_template('workflow_builder.html', workflow=draft, tools=all_tools,
                           templates=all_templates,
                           params_text=json.dumps(_draft_params(draft), indent=2))


@workflow_bp.route('/draft/<workflow_id>/params', methods=['POST'])
@login_required
def set_draft_params(workflow_id):
    """The builder's parameters panel: a JSON object."""
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    text = request.form.get("params", "").strip() or "{}"
    try:
        params = json.loads(text)
        if not isinstance(params, dict):
            raise ValueError("must be a JSON object")
    except ValueError as e:
        flash(f"Parameters not saved: {e}", "error")
        return redirect(url_for('workflow.builder', workflow_id=workflow_id))
    draft.params = json.dumps(params)
    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=workflow_id))


@workflow_bp.route('/workflow/<workflow_id>/add-tool/<int:tool_id>', methods=['POST'])
@login_required
def add_tool_to_workflow(workflow_id, tool_id):
    tool = Tool.query.get_or_404(tool_id)
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)

    new_task = TemporaryTask()
    new_task.workflow_id = workflow_id
    new_task.tool_id = tool_id
    new_task.workflow_position = len(draft.tasks)
    new_task.settings = settings_engine.default_settings(tool)
    new_task.priority = tool.default_priority
    new_task.weight = tool.default_weight
    db.session.add(new_task)
    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=workflow_id))


@workflow_bp.route('/workflow/task/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = TemporaryTask.query.get_or_404(task_id)
    wf_id = task.workflow_id
    db.session.delete(task)
    db.session.commit()

    # Re-normalize positions so there are no gaps (e.g., 0, 1, 3 -> 0, 1, 2)
    remaining = TemporaryTask.query.filter_by(workflow_id=wf_id)\
        .order_by(TemporaryTask.workflow_position).all()
    for i, t in enumerate(remaining):
        t.workflow_position = i
    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=wf_id))


@workflow_bp.route('/workflow/task/<int:task_id>/move/<direction>', methods=['POST'])
@login_required
def move_task(task_id, direction):
    task = TemporaryTask.query.get_or_404(task_id)
    wf_id = task.workflow_id
    current_pos = task.workflow_position

    if direction == 'up' and current_pos > 0:
        neighbor = TemporaryTask.query.filter_by(
            workflow_id=wf_id, workflow_position=current_pos - 1).first()
        if neighbor:
            task.workflow_position -= 1
            neighbor.workflow_position += 1
    elif direction == 'down':
        neighbor = TemporaryTask.query.filter_by(
            workflow_id=wf_id, workflow_position=current_pos + 1).first()
        if neighbor:
            task.workflow_position += 1
            neighbor.workflow_position -= 1

    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=wf_id))


def _after_edit(task):
    """Where the settings editor returns to: the review for a guided
    draft, the builder otherwise (or whatever ?next= says)."""
    target = request.args.get("next") or request.form.get("next")
    if target in ("review", "builder"):
        return redirect(url_for(f'workflow.{target}', workflow_id=task.workflow_id))
    if task.workflow.template_id:
        return redirect(url_for('workflow.review', workflow_id=task.workflow_id))
    return redirect(url_for('workflow.builder', workflow_id=task.workflow_id))


@workflow_bp.route('/edit-settings/<int:task_id>', methods=['GET', 'POST'])
@login_required
def edit_settings(task_id):
    """A step's settings. Structured tools get a form from their schema,
    or a raw JSON editor — the only way to write $from/$param references,
    and the default when the settings already contain some."""
    task = TemporaryTask.query.get_or_404(task_id)
    tool = task.tool
    params = _draft_params(task.workflow)
    schema = None
    if tool.settings_schema and (tool.settings_format or "opaque") != "opaque":
        schema = json.loads(tool.settings_schema)
    structured = (tool.settings_format or "opaque") != "opaque"
    mode = request.values.get("mode")
    if mode not in ("form", "json"):
        mode = "json" if (not schema or _has_refs(task.settings)) else "form"
    if not structured:
        mode = "text"

    def render(values=None, text=None, errors=None):
        if values is None:
            values = _json_or(task.settings, {}) if structured else {}
        if text is None:
            text = task.settings
        return render_template('workflow_settings.html', task=task, schema=schema,
                               values=values, text=text, mode=mode,
                               structured=structured, errors=errors,
                               params=params, next=request.values.get("next"))

    if request.method == 'POST':
        task.priority = request.form.get('priority', type=int)
        task.weight = request.form.get('weight', type=int)

        if mode == "form":
            values = _settings_from_form(schema, request.form)
            errors = settings_engine.validate(
                tool, artifacts.validation_candidate(tool, values, params))
            if errors:
                db.session.rollback()
                return render(values=values, errors=errors)
            task.settings = json.dumps(values, indent=2)
        elif mode == "json":
            text = request.form.get('settings_string', '')
            try:
                values = json.loads(text) if text.strip() else {}
                if not isinstance(values, dict):
                    raise ValueError("settings must be a JSON object")
                errors = settings_engine.validate(
                    tool, artifacts.validation_candidate(tool, values, params))
            except ValueError as e:
                errors = [str(e)]
            if errors:
                db.session.rollback()
                return render(text=text, errors=errors)
            task.settings = json.dumps(values, indent=2)
        else:
            task.settings = clean_string(request.form.get('settings_string', ''))

        db.session.commit()
        return _after_edit(task)

    return render()


@workflow_bp.route('/workflow/finalize/<workflow_id>', methods=['POST'])
@login_required
def finalize_workflow(workflow_id):
    """Start the run. Goes through create_new_workflow so references and
    parameters are checked like an API submission; a problem returns to
    the draft with the message rather than losing it."""
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    name = request.form.get('workflow_name', '').strip() or draft.name or 'Unnamed'
    template = templates.find(draft.template_id) if draft.template_id else None
    back = url_for('workflow.review' if draft.template_id else 'workflow.builder',
                   workflow_id=draft.id)
    if not draft.tasks:
        flash("Add at least one step first.", "error")
        return redirect(back)
    try:
        new_id = create_new_workflow(name, _draft_specs(draft),
                                     _draft_params(draft), template)
    except (KeyError, ValueError) as e:
        db.session.rollback()
        # args[0] rather than str(e): KeyError's str() wraps it in quotes.
        flash(e.args[0] if e.args else str(e), "error")
        return redirect(back)
    db.session.delete(draft)
    db.session.commit()
    return redirect(url_for('processing.workflow_detail', workflow_id=new_id))


# ---- templates from the builder ----------------------------------------------------

@workflow_bp.route('/workflow/save-template/<workflow_id>', methods=['POST'])
@login_required
def save_as_template(workflow_id):
    """Copy the draft into a user-saved template, keeping the draft. A
    draft made from a template inherits its parameter schema."""
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    template_name = request.form.get('template_name', '').strip() or 'Unnamed Template'
    source = templates.find(draft.template_id) if draft.template_id else None
    try:
        templates.save(template_name, _draft_specs(draft),
                       _json_or(source.params_schema, None) if source else None,
                       source.description if source else None,
                       source.name_format if source else None,
                       current_user.id)
        flash(f"Saved template '{template_name}'.", "success")
    except templates.TemplateError as e:
        flash(str(e), "error")
    back = 'workflow.review' if draft.template_id else 'workflow.builder'
    return redirect(url_for(back, workflow_id=workflow_id))


@workflow_bp.route('/workflow/<workflow_id>/apply-template/<template_id>', methods=['POST'])
@login_required
def apply_template_to_draft(workflow_id, template_id):
    """Replace the builder draft's steps with a template's."""
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    template = TemplateWorkflow.query.get_or_404(template_id)
    _fill_from_template(draft, template)
    if not draft.name:
        draft.name = f"Draft from {template.name}"
    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=workflow_id))
