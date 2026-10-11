FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 DB_PATH=/var/data/macromind.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser && mkdir -p /var/data && chown appuser:appuser /var/data
COPY --chown=appuser:appuser app.py wsgi.py gunicorn.conf.py docker_entrypoint.py ./
COPY --chown=appuser:appuser static ./static
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/api/health',timeout=4)"
CMD ["python", "docker_entrypoint.py"]
