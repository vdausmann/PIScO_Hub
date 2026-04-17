from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required
from ..backend.models import GlobalSetting, db


settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def manage_settings():
    if request.method == 'POST':
        # Iterate through all settings in the DB and check if they are in the form
        all_settings = GlobalSetting.query.all()
        for setting in all_settings:
            new_val = request.form.get(setting.key)
            if new_val is not None:
                setting.value = new_val
        
        db.session.commit()
        flash("Settings updated successfully!", "success")
        return redirect(url_for('settings.manage_settings'))

    settings = GlobalSetting.query.all()
    return render_template('settings.html', settings=settings)
