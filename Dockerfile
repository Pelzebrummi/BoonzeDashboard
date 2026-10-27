FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

ARG BUILD_VERSION=dev
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="amd64"

EXPOSE 8099
CMD ["python", "-m", "app"]
