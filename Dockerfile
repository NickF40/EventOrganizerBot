FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .

ENV CONFIG_FILE=/app/config.yaml \
    PYTHONPATH=/app
EXPOSE 8000

CMD ["uv", "run", "python", "-m", "app.main"]
