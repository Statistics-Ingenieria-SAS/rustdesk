FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV STATS_DB=/data/stats.sqlite3
ENV PEERS_DB=/peers/db_v2.sqlite3
ENV HBBS_CONTAINER=hbbs
ENV HBBR_CONTAINER=hbbr

EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]