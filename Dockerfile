FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
COPY riffdb ./riffdb
RUN uv pip install --system --no-cache .
ENV RIFFDB_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "riffdb.main:app", "--host", "0.0.0.0", "--port", "8000"]
