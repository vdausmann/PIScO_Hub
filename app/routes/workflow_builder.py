from math import log
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from ..backend.models import TemporaryWorkflow, Workflow, db, Tool, TemporaryTask, Task

workflow_bp = Blueprint('workflow', __name__)


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
    
    all_tools = Tool.query.all()
    
    return render_template('workflow_builder.html', 
                           workflow=draft, 
                           tools=all_tools)

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
    new_task.settings = tool.settings_template
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
    
    if request.method == 'POST':
        # 2. Update the task with form data
        task.priority = request.form.get('priority', type=int)
        task.weight = request.form.get('weight', type=int)
        task.settings = request.form.get('settings_string')
        
        db.session.commit()
        
        # 3. Redirect back to the builder for the parent workflow
        return redirect(url_for('workflow.builder', workflow_id=task.workflow_id))
    
    # GET request: Show the editing page
    return render_template('workflow_settings.html', task=task)


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
