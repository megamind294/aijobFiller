"""Account-scoped lane settings. Saving a lane never activates it."""
from flask import current_app, redirect, render_template, request, url_for

from .automation import Automation
from .web_applications import application_action, billing_context
from .workspace import current_store


def current_automation():
    ledger, account_id, owner = billing_context()
    subscription = ledger.subscription(account_id)
    entitled = subscription.get('tier') == 'power' and subscription.get('status') in ('active', 'trialing')
    return Automation(current_store(), ledger, account_id, owner=owner, entitled=entitled,
                      adapters=current_app.config.get('JOBPILOT_APPLICATION_ADAPTERS', {}))


def register_automation(app):
    @app.get('/auto-apply')
    @app.get('/dashboard/auto-apply')
    def automation_index():
        service = current_automation()
        from .profile_vault import ProfileVault
        profiles = ProfileVault(current_store().home).list()
        try:
            preview, error = service.preview(), ''
        except (ValueError, FileNotFoundError) as exc:
            preview, error = {**service.settings(), 'candidates': [], 'day': '', 'schedule': 'On demand'}, str(exc)
        return render_template('automation.html', lanes=service.lanes(), preview=preview, history=service.history(),
                               entitled=service.entitled or service.owner, error=error, profiles=profiles)

    @app.post('/auto-apply/lanes')
    @application_action
    def automation_save_lane():
        def lines(name):
            return [item.strip() for item in request.form.get(name, '').splitlines() if item.strip()]
        current_automation().save_lane({'name': request.form.get('name', ''), 'mode': request.form.get('mode', 'review-first'),
            'keywords': lines('keywords'), 'locations': lines('locations'), 'exclusions': lines('exclusions'),
            'optimization': request.form.get('optimization', 'honest'), 'threshold': int(request.form.get('threshold', 0)),
            'resume_profile': request.form.get('resume_profile', 'default'),
            'cover_letter': request.form.get('cover_letter') == 'yes'}, request.form.get('lane_id') or None)
        return redirect(url_for('automation_index'))

    @app.post('/auto-apply/lanes/<identifier>/activate')
    @application_action
    def automation_activate(identifier):
        # Explicit button activation authorizes only the saved lane rules shown on this page.
        current_automation().activate(identifier, request.form.get('active') == 'yes')
        return redirect(url_for('automation_index'))

    @app.post('/auto-apply/limits')
    @application_action
    def automation_limits():
        current_automation().settings(int(request.form.get('daily_cap', 10)), request.form.get('timezone', 'UTC'))
        return redirect(url_for('automation_index'))

    @app.post('/auto-apply/run')
    @application_action
    def automation_run():
        current_automation().run()
        return redirect(url_for('automation_index'))
