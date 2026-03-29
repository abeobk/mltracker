import os
from flask import Flask, jsonify, send_from_directory, abort, g
from config import Config
from db import get_db, close_db, init_db, migrate_db


def create_app(config=None):
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(__file__), '..', 'frontend'),
        static_url_path='',
    )
    app.config.from_object(config or Config)

    # DB lifecycle
    init_db(app)
    migrate_db(app)
    app.teardown_appcontext(close_db)

    # Rate limiter (Redis-backed; must be initialised before blueprints)
    from limiter import limiter
    limiter.init_app(app)

    # Auth (OAuth client + blueprints)
    from auth import init_oauth, auth_bp
    init_oauth(app)
    app.register_blueprint(auth_bp, url_prefix='/auth')

    # API blueprints
    from routes.api      import api_bp
    from routes.projects import projects_bp
    from routes.runs     import runs_bp
    from routes.admin    import admin_bp
    app.register_blueprint(api_bp,      url_prefix='/api/v1')
    app.register_blueprint(projects_bp, url_prefix='/api/v1')
    app.register_blueprint(runs_bp,     url_prefix='/api/v1')
    app.register_blueprint(admin_bp,    url_prefix='/api/v1')

    # Health endpoint — deep check (verifies DB is reachable)
    @app.get('/health')
    def health():
        try:
            get_db().execute("SELECT 1")
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "detail": str(e)}), 500

    # Serve stored image files (login required)
    from auth import login_required as _login_required

    @app.route('/files/<path:rel_path>')
    @_login_required
    def serve_file(rel_path):
        return send_from_directory(app.config['FILES_DIR'], rel_path)

    # SDK install redirect — no auth; lives under /api/v1/ so Nginx already proxies it.
    # Redirects to the actual versioned wheel filename so pip reads a valid name.
    @app.get('/api/v1/sdk')
    def sdk_redirect():
        from flask import redirect as _redirect
        downloads_dir = os.path.join(app.static_folder, 'downloads')
        if not os.path.isdir(downloads_dir):
            abort(404)
        wheels = sorted(
            (f for f in os.listdir(downloads_dir)
             if f.endswith('.whl') and f != 'mltracker-latest.whl'),
            key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)),
            reverse=True,
        )
        if not wheels:
            abort(404)
        return _redirect(f'/downloads/{wheels[0]}')

    # SPA catch-all — must come LAST; guards API prefixes so missing routes stay JSON 404
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def spa(path):
        if path.startswith(('api/', 'auth/', 'files/', 'health')):
            abort(404)
        # login.html served directly; everything else gets the SPA shell
        if path == 'login.html':
            return send_from_directory(app.static_folder, 'login.html')
        return send_from_directory(app.static_folder, 'index.html')

    # Catch-all JSON error handler
    @app.errorhandler(Exception)
    def handle_exception(e):
        code = getattr(e, 'code', 500)
        return jsonify({'error': str(e)}), code

    return app
