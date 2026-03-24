import multiprocessing

# Bind only to localhost — Nginx proxies externally
bind        = '127.0.0.1:8000'

# Sync workers safe with SQLite WAL; do NOT use gevent/eventlet
worker_class = 'sync'
workers      = 4   # 2 × CPU cores for a t3.small

# Generous timeout for large image uploads (20 MB)
timeout      = 120
keepalive    = 5

# Logging
accesslog = '/var/log/gunicorn/access.log'
errorlog  = '/var/log/gunicorn/error.log'
loglevel  = 'info'

# Recycle workers periodically to prevent memory leaks
max_requests          = 1000
max_requests_jitter   = 100
