import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.factory import create_app
from app.backend.models import db, Task, Workflow, Tool

app = create_app(False)

def reset_tasks_only():
    with app.app_context():
        print("[*] Clearing Tasks and Steps...")
        
        db.session.query(Task).delete()
        db.session.query(Workflow).delete()
        db.session.query(Tool).delete()

        db.session.commit()
        
        print("[+] Processing history cleared. Users preserved.")

if __name__ == "__main__":
    reset_tasks_only()
