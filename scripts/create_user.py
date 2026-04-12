import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.factory import create_app
from app.models import db, User
from werkzeug.security import generate_password_hash

app = create_app()

def create_admin():
    with app.app_context():
        db.create_all()
        username = input("Username: ")
        password = input("Password: ")
        hashed_pw = generate_password_hash(password, method='scrypt')
        user = User(username=username, password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        print("Done.")

if __name__ == "__main__":
    create_admin()
