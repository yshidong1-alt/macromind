import os

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1  # one scheduler and one SQLite writer process per persistent disk
worker_class = "gthread"
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
# Log paths without query strings; never log authentication headers or bodies.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
