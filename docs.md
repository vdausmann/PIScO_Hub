

## Tools
Tools are predefined configurations of programs which can be run by the server.
A tool is defined by a config.json file following the template:

```json
{
	"name": "example_tool",
	"execution_call": "python3 /path/to/program.py",
    "settings_template_path": "path/to/settings/template",
    "description": "This is a short description of this tool",
    "priority": 1,
    "weight": 1
}
```

When the tool is added as a task the user will first have to define the settings if a 
settings template is given. When the task is called, the server appends the settings file 
defined by the user to the execution call and runs this call in a separated process. On 
startup the server will register all tools within the *tools* folder.

The priority is a default value and ensures that high priority tools are run as soon as
possible. In addition, the weight is a value to ensure that the computational demand on
the server stays within in certain limit, i.e. not too many tools run in parallel.

## Task
A task is the combination of a tool together with an optional settings file defined by the
user. The task can be executed by the server.


## Workflows
A workflow is a collection of **task** that should be run in a specific order. Only one
task can be run at the same time to ensure correct ordering. The **taskManager** ensures
the parallel execution of tasks across different workflows. A workflow is defined via a
config.json file:

```json
{
	"name": "Example workflow",
	"tasks": 
	[
		"download",
		"segment"
	],
    "description": "This is a short description of this workflow"
}
```

The ordering within the *tasks* list is important as this order will be used to call the
tasks. The tasks are referenced by their name.
