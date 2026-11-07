FROM python:3.12-slim
RUN groupadd --gid 10001 vion && useradd --uid 10001 --gid vion --no-create-home vion && mkdir /data && chown vion:vion /data
WORKDIR /app
COPY collector.py /app/collector.py
USER 10001:10001
VOLUME /data
EXPOSE 8790
ENTRYPOINT ["python", "-u", "/app/collector.py", "--data", "/data", "--config", "/data/config.json", "--host", "0.0.0.0", "--port", "8790"]
