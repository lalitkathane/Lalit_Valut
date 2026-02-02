import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here-make-it-long'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'instance', 'bachat_gat.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Add these for better static file handling
    STATIC_FOLDER = 'static'
    STATIC_URL_PATH = '/static'