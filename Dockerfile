FROM docker.io/python:3.12 AS base
ENV PYTHONUNBUFFERED=1
RUN apt-get -y update && apt-get install -y ffmpeg
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /conversion
COPY pyproject.toml .
RUN uv lock
RUN uv sync --locked --group services
