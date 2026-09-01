-- Script de inicializacion de PostgreSQL (corre como usuario vision).
-- El usuario y la BD los crea docker-entrypoint (POSTGRES_USER/POSTGRES_DB).
-- NOTA: las tablas/indices los crea `alembic upgrade head` (install.sh).

CREATE EXTENSION IF NOT EXISTS pgcrypto;
