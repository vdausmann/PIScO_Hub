from ..utils import clean_string
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from ..backend import settings_engine
from ..backend.models import (TemporaryWorkflow, Workflow, db, Tool, TemporaryTask, Task,
                              TemplateTask, TemplateWorkflow)
import json

workflow_bp = Blueprint('workflow', __name__)


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


@workflow_bp.route('/workflow/init', methods=["POST"])
@login_required
def init_workflow():
    # first check if a temporary workflow already exists
    if TemporaryWorkflow.query.count() > 0:
        workflow = TemporaryWorkflow.query.first()
        if not isinstance(workflow, TemporaryWorkflow):
            raise Exception("TemporaryWorkflow query returned non-TemporaryWorkflow object")
        return redirect(url_for("workflow.builder",
                                workflow_id=workflow.id))

    new_workflow = TemporaryWorkflow()
    new_workflow.user_id = current_user.id

    db.session.add(new_workflow)
    db.session.commit()

    return redirect(url_for("workflow.builder", workflow_id=new_workflow.id))

@workflow_bp.route('/builder/<workflow_id>')
@login_required
def builder(workflow_id):
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)

    all_tools = Tool.query.filter(Tool.available != False).order_by(Tool.name).all()
    # Pull all templates so the user can select them inside the UI
    all_templates = TemplateWorkflow.query.all()
    
    return render_template('workflow_builder.html', 
                           workflow=draft, 
                           tools=all_tools,
                           templates=all_templates)

@workflow_bp.route('/workflow/<workflow_id>/add-tool/<int:tool_id>', methods=['POST'])
@login_required
def add_tool_to_workflow(workflow_id, tool_id):

    tool = Tool.query.get_or_404(tool_id)
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)

    if not isinstance(tool, Tool):
        raise Exception("Tool query returned non-Tool object")

    # Calculate the next order index
    next_index = len(draft.tasks)

    # Create the temporary task using tool defaults
    new_task = TemporaryTask()
    new_task.workflow_id = workflow_id
    new_task.tool_id = tool_id
    new_task.workflow_position = next_index
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
    remaining_tasks = TemporaryTask.query.filter_by(workflow_id=wf_id)\
        .order_by(TemporaryTask.workflow_position).all()
    
    for i, t in enumerate(remaining_tasks):
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
        # Find the task currently above it
        neighbor = TemporaryTask.query.filter_by(
            workflow_id=wf_id, 
            workflow_position=current_pos - 1
        ).first()
        if neighbor:
            task.workflow_position -= 1
            neighbor.workflow_position += 1
            
    elif direction == 'down':
        # Find the task currently below it
        neighbor = TemporaryTask.query.filter_by(
            workflow_id=wf_id, 
            workflow_position=current_pos + 1
        ).first()
        if neighbor:
            task.workflow_position += 1
            neighbor.workflow_position -= 1
            
    db.session.commit()
    return redirect(url_for('workflow.builder', workflow_id=wf_id))



@workflow_bp.route('/edit-settings/<int:task_id>', methods=['GET', 'POST'])
@login_required
def edit_settings(task_id):
    # 1. Get the specific task from the temporary table
    task = TemporaryTask.query.get_or_404(task_id)

    if not isinstance(task, TemporaryTask):
        return "Task not found", 404

    tool = task.tool
    schema = None
    if tool.settings_schema and (tool.settings_format or "opaque") != "opaque":
        schema = json.loads(tool.settings_schema)

    if request.method == 'POST':
        # 2. Update the task with form data
        task.priority = request.form.get('priority', type=int)
        task.weight = request.form.get('weight', type=int)

        if schema is not None and request.form.get('form_mode') == 'schema':
            values = _settings_from_form(schema, request.form)
            errors = settings_engine.validate(tool, values)
            if errors:
                db.session.rollback()
                return render_template('workflow_settings.html', task=task,
                                       schema=schema, values=values, errors=errors)
            task.settings = json.dumps(values, indent=2)
        else:
            settings = request.form.get('settings_string')
            task.settings = clean_string(settings)

        db.session.commit()

        # 3. Redirect back to the builder for the parent workflow
        return redirect(url_for('workflow.builder', workflow_id=task.workflow_id))

    # GET request: Show the editing page
    values = {}
    if schema is not None:
        try:
            values = json.loads(task.settings) if task.settings else {}
        except ValueError:
            values = {}
    return render_template('workflow_settings.html', task=task,
                           schema=schema, values=values, errors=None)


@workflow_bp.route('/workflow/finalize/<workflow_id>', methods=['POST'])
@login_required
def finalize_workflow(workflow_id):
    temporary_workflow = TemporaryWorkflow.query.get_or_404(workflow_id)
    new_name = request.form.get('workflow_name', 'Unnamed')

    try:
        final_wf = Workflow()
        final_wf.name = new_name
        final_wf.user_id = temporary_workflow.user_id
        db.session.add(final_wf)
        db.session.flush() # Flushes to get the final_wf.id for the tasks

        for temp_task in temporary_workflow.tasks:
            new_task = Task()
            new_task.workflow_id = final_wf.id
            new_task.tool_id = temp_task.tool_id
            new_task.workflow_position = temp_task.workflow_position
            new_task.priority = temp_task.priority
            new_task.weight = temp_task.weight
            new_task.settings = temp_task.settings

            db.session.add(new_task)

        db.session.delete(temporary_workflow)
        
        db.session.commit()
        print(url_for('processing.dashboard'))
        return redirect(url_for('processing.dashboard'))

    except Exception as e:
        db.session.rollback()
        print(f"Finalization Error: {e}")
        return "Failed to finalize workflow", 500


@workflow_bp.route('/workflow/save-template/<workflow_id>', methods=['POST'])
@login_required
def save_as_template(workflow_id):
    """
    Takes the current TemporaryWorkflow state and copies it into 
    the Template tables without destroying the draft.
    """
    temporary_workflow = TemporaryWorkflow.query.get_or_404(workflow_id)
    template_name = request.form.get('template_name', 'Unnamed Template')

    try:
        # Create the top-level template
        template_wf = TemplateWorkflow()
        template_wf.name = template_name
        template_wf.user_id = current_user.id
        
        db.session.add(template_wf)
        db.session.flush()  # Aquire template_wf.id

        # Copy over the sequence
        for temp_task in temporary_workflow.tasks:
            tpl_task = TemplateTask()
            tpl_task.workflow_id = template_wf.id
            tpl_task.tool_id = temp_task.tool_id
            tpl_task.workflow_position = temp_task.workflow_position
            tpl_task.priority = temp_task.priority
            tpl_task.weight = temp_task.weight
            tpl_task.settings = temp_task.settings
            
            db.session.add(tpl_task)

        db.session.commit()
        return redirect(url_for('workflow.builder', workflow_id=workflow_id))

    except Exception as e:
        db.session.rollback()
        print(f"Template Saving Error: {e}")
        return "Failed to save template", 500


@workflow_bp.route('/workflow/load-template/<template_id>', methods=['POST'])
@login_required
def load_template(template_id):
    """
    Initializes a completely new workspace draft populated 
    with configuration components pulled straight from a saved template.
    """
    template = TemplateWorkflow.query.get_or_404(template_id)
    
    # Optional: Clear existing temporary workflows first if your application rules limit users to 1 sandbox 
    TemporaryWorkflow.query.filter_by(user_id=current_user.id).delete()

    try:
        new_workflow = TemporaryWorkflow()
        new_workflow.name = f"Draft from {template.name}"
        new_workflow.user_id = current_user.id
        
        db.session.add(new_workflow)
        db.session.flush()

        for tpl_task in template.tasks:
            temp_task = TemporaryTask()
            temp_task.workflow_id = new_workflow.id
            temp_task.tool_id = tpl_task.tool_id
            temp_task.workflow_position = tpl_task.workflow_position
            temp_task.priority = tpl_task.priority
            temp_task.weight = tpl_task.weight
            temp_task.settings = tpl_task.settings
            
            db.session.add(temp_task)

        db.session.commit()
        return redirect(url_for('workflow.builder', workflow_id=new_workflow.id))

    except Exception as e:
        db.session.rollback()
        print(f"Template Loading Error: {e}")
        return "Failed to load template into workspace", 500


@workflow_bp.route('/workflow/<workflow_id>/apply-template/<template_id>', methods=['POST'])
@login_required
def apply_template_to_draft(workflow_id, template_id):
    """
    Clears out the tasks in the active workspace and appends 
    all structural steps directly out of an existing system template preset.
    """
    draft = TemporaryWorkflow.query.get_or_404(workflow_id)
    template = TemplateWorkflow.query.get_or_404(template_id)
    
    try:
        # 1. Purge all existing tasks in this workspace to start clean
        TemporaryTask.query.filter_by(workflow_id=workflow_id).delete()
        
        # 2. Update the default draft title text to reference your structural preset
        draft.name = f"Draft from {template.name}"
        
        # 3. Duplicate individual components from the template array to your active workspace 
        for tpl_task in template.tasks:
            temp_task = TemporaryTask()
            temp_task.workflow_id = draft.id
            temp_task.tool_id = tpl_task.tool_id
            temp_task.workflow_position = tpl_task.workflow_position
            temp_task.priority = tpl_task.priority
            temp_task.weight = tpl_task.weight
            temp_task.settings = tpl_task.settings
            
            db.session.add(temp_task)
            
        db.session.commit()
        return redirect(url_for('workflow.builder', workflow_id=workflow_id))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error applying template configuration payload: {e}")
        return "Failed to import structural configurations into layout workspace", 500
