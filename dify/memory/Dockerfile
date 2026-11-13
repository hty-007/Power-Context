FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir 'powercontext[cli,server]==1.4.1' \
    && useradd --create-home --uid 10001 app \
    && install -d -o app -g app /data

WORKDIR /app
COPY server.py .
USER app
EXPOSE 8079
CMD ["python", "server.py", "--host", "0.0.0.0", "--data", "/data"]
