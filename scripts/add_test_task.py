import sys
import os
# Ensure we can import from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.factory import create_app
from app.models import db, Task, Workflow

app = create_app()

def add_test_data():
    with app.app_context():
        db.create_all()
        # 1. Create a Task Container
        new_task = Workflow(
            name="Alpha Signal Analysis",
            status="Pending"
        )
        db.session.add(new_task)
        db.session.flush() # This gets us the task.id before committing

        # 2. Add a Low Priority Step (Priority 1)
        step_1 = Task(
            workflow_id=new_task.id,
            module_name="c_analyzer",
            priority=1,
            weight=8,
            status="Pending",
            config_snapshot="threshold=0.5\nmode=fast"
        )

        # 3. Add a High Priority Step (Priority 10)
        # Even if this is added second, the worker should grab this first
        step_2 = Task(
            workflow_id=new_task.id,
            module_name="segmenter",
            priority=10,
            weight=2,
            status="Pending",
            config_snapshot="input=raw_data.dat\ncleanup=true"
        )

        db.session.add(step_1)
        db.session.add(step_2)
        db.session.commit()

        print(f"[*] Workflow '{new_task.name}' created with 2 steps.")
        print(f"[*] Task 1 (ID: {step_1.id}) Priority: {step_1.priority}")
        print(f"[*] Task 2 (ID: {step_2.id}) Priority: {step_2.priority}")

if __name__ == "__main__":
    add_test_data()
