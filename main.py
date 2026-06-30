from flask import render_template
from flask_login import login_required
from app.factory import create_app

app = create_app()

@app.route('/')
@login_required
def index():

    for rule in app.url_map.iter_rules():
        print(rule.endpoint, rule)

    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)

