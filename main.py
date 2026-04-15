from flask import render_template
from flask_login import login_required
from app.factory import create_app
from app.backend.models import db

app = create_app()

@app.route('/')
@login_required
def index():
    return render_template('index.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
