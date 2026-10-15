FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home jobpilot
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY jobpilot ./jobpilot
RUN pip install --no-cache-dir . && mkdir /data && chown jobpilot:jobpilot /data
USER jobpilot
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "jobpilot", "--home", "/data"]
CMD ["watch", "--interval", "1200", "--prepare"]
