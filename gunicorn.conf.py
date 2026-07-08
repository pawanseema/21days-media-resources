import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gthread"
workers = 1
threads = 8
timeout = 0
