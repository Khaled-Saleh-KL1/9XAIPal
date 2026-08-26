# PostgreSQL Docker Design

## Purpose

This directory contains local PostgreSQL infrastructure configuration for 9XAIPal.

The database is expected to run through `backend/docker-compose.yml` and use the `pgvector/pgvector` image so the vector extension is available without manual host setup.

## Files

### `init/01-enable-pgvector.sql`

Initialization SQL mounted into `/docker-entrypoint-initdb.d`. It enables the `vector` extension when the database volume is created for the first time.

## Data Dependencies

`core.config` reads database connection settings from environment variables.

`database.connection` uses those settings to create PostgreSQL connections.

`database.pgvector` assumes the `vector` extension is enabled by this initialization layer and verified by migrations or startup checks.

