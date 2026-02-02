from flask import Flask
from app.extensions import db, login_manager
from config import Config
import os  # ADD THIS IMPORT


def create_app():
    app = Flask(__name__,static_folder='static', static_url_path='/static')
    app.config.from_object(Config)

    # ADD THIS: Ensure instance folder exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        print(f"✅ Instance folder created/verified: {app.instance_path}")
    except OSError as e:
        print(f"⚠️  Could not create instance folder: {e}")

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)

    # User loader
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.context_processors import auth_context_processor

    # ... after creating app
    app.context_processor(auth_context_processor)
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.groups import groups_bp
    from app.routes.loans import loans_bp
    from app.routes.wallet import wallet_bp
    from app.routes.admin import admin_bp  # NEW

    app.register_blueprint(auth_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(admin_bp)  # NEW

    # Create tables
    with app.app_context():
        db.create_all()
        print("✅ Database tables created!")

    return app