from flask import render_template
from flask_login import login_required
from app.factory import create_app
import os

app = create_app()

@app.route('/')
@login_required
def index():
    return render_template('index.html')

if __name__ == '__main__':
    # Debug off unless asked for: the reloader restarts the process, which
    # would restart the scheduler threads underneath running tasks.
    debug = os.environ.get("HUB_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=os.environ.get("HUB_HOST", "0.0.0.0"),
            port=int(os.environ.get("HUB_PORT", 5000)),
            debug=debug, use_reloader=debug)

