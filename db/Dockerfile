# Postgres 18 with Apache AGE (prebuilt upstream image) + pgvector (PGDG package).
FROM apache/age:release_PG18_1.8.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-18-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY init-extensions.sql /docker-entrypoint-initdb.d/10-init-extensions.sql
