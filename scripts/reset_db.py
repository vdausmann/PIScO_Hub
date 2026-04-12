import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.factory import create_app
from app.models import db, Task, Workflow

app = create_app(run_worker=False)

def reset_tasks_only():
    with app.app_context():
        print("[*] Clearing Tasks and Steps...")
        
        # Delete all steps first due to Foreign Key constraints
        db.session.query(Task).delete()
        # Then delete the tasks
        db.session.query(Workflow).delete()
        
        print("[*] Re-inserting test data...")
        
        test_task = Workflow(
            name="Targeted Reset Test",
            status="Pending"
        )
        db.session.add(test_task)
        db.session.flush()

        # Adding test steps with weights
        s1 = Task(workflow_id=test_task.id, module_name="segmenter", priority=10, weight=9, status="Pending")
        s2 = Task(workflow_id=test_task.id, module_name="c_analyzer", priority=1, weight=2, status="Pending")

        db.session.add_all([s1, s2])
        db.session.commit()
        
        print("[+] Processing history cleared. Users preserved.")

if __name__ == "__main__":
    reset_tasks_only()
