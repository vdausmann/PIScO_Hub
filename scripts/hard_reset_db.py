import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.factory import create_app
from app.backend.models import db, Task, Workflow, Tool, User

app = create_app(False)

def migrate_and_restore():
    with app.app_context():
        # 1. Extract Users
        print("[*] Extracting users...")
        users_data = []
        for user in User.query.all():
            users_data.append({
                'username': user.username,
                'password': user.password,
            })

        # 2. Drop and Recreate
        print("[*] Dropping and recreating database...")
        db.drop_all()
        db.create_all()

        # 3. Restore Users
        print(f"[*] Restoring {len(users_data)} users...")
        for data in users_data:
            new_user = User(**data)
            db.session.add(new_user)
        
        db.session.commit()
        print("[+] Migration complete.")

if __name__ == "__main__":
    migrate_and_restore()
