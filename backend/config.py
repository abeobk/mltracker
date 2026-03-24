import os


class Config:
    SECRET_KEY = os.environ['SECRET_KEY']  # raises at startup if missing

    DB_PATH    = os.environ.get('DB_PATH',    os.path.join(os.path.dirname(__file__), '..', 'data', 'wandb.db'))
    FILES_DIR  = os.environ.get('FILES_DIR',  os.path.join(os.path.dirname(__file__), '..', 'data', 'mywandb'))

    GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID',     'fake')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'fake')

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE   = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-secret'
    SESSION_COOKIE_SECURE = False
    GOOGLE_CLIENT_ID     = 'fake'
    GOOGLE_CLIENT_SECRET = 'fake'
