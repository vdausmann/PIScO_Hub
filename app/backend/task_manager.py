from .tools import Tools
from .models import db, Workflow, Task

MAX_WEIGHT = 10

TOOLS = Tools()
TOOLS.register_tools()



def create_new_workflow(workflow_name: str, tools: list[str], settings:
                        list[str]):
    # Load workflow config and create tasks from tools
    # 1. Create workflow in db
    # 2. Create a task in the db for each tool
    new_workflow = Workflow()
    new_workflow.name = workflow_name

    db.session.add(new_workflow)
    db.session.flush() # This gets us the task.id before committing

    for i, tool_name in enumerate(tools):
        tool = TOOLS.get_tool(tool_name)
        task = Task()
        task.workflow_id = new_workflow.id
        task.tool_name = tool.name
        task.priority = tool.priority
        task.weight = tool.weight
        task.settings_file_path = settings[i]

        db.session.add(task)

    db.session.commit()



    return new_workflow.id
