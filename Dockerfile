# SCMS application image (SCMS-NF-006).
#
# Two stages. The build stage compiles any wheels that need a C
# toolchain -- psycopg, argon2-cffi and pillow all do -- and the
# runtime stage copies only the installed packages. That keeps the
# compiler out of the shipped image, which is both smaller and one
# fewer thing an attacker can use.

FROM python:3.11-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

# libpq5 is the runtime half of libpq-dev: psycopg needs it, the
# headers and compiler it was built against are not shipped.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

# A non-root user, because a container that runs as root turns any
# code-execution bug into root on the container filesystem.
RUN useradd --create-home --uid 10001 scms

COPY --from=build /install /usr/local
WORKDIR /app
COPY --chown=scms:scms . .

# Uploaded evidence lives outside the web root (SCMS-SR-005) and
# must survive a container being replaced, so it is a volume.
RUN mkdir -p /app/private-media && chown scms:scms /app/private-media
VOLUME ["/app/private-media"]

USER scms
WORKDIR /app/src
EXPOSE 8000

# gunicorn, not runserver: Django's development server is single
# threaded and explicitly not for production.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-"]
