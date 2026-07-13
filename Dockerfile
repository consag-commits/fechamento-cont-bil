FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN uv run python manage.py collectstatic --noinput

EXPOSE 8000

CMD sh -c "uv run python manage.py migrate --noinput && uv run gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000}"
