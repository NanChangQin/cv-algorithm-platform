# Incremental release using previously built platform images and cached dependencies.
# For clean installations use the service Dockerfiles instead.
ARG BACKEND_BASE=cv-platform/backend:dev
ARG MANAGER_BASE=cv-platform/algorithm-manager:dev
ARG DATABASE_BASE=cv-platform/database-migrator:dev
ARG FRONTEND_BASE=cv-platform/frontend:dev

FROM ${BACKEND_BASE} AS backend-update
COPY backend /workspace/backend
COPY backend/src/cv_platform /usr/local/lib/python3.12/site-packages/cv_platform

FROM ${MANAGER_BASE} AS manager-update
COPY services/algorithm-manager /workspace/services/algorithm-manager
COPY services/algorithm-manager/src/algorithm_manager /usr/local/lib/python3.12/site-packages/algorithm_manager
# The existing Backend already contains python-multipart, now also used by Manager.
COPY --from=backend-update /usr/local/lib/python3.12/site-packages/python_multipart /usr/local/lib/python3.12/site-packages/python_multipart
COPY --from=backend-update /usr/local/lib/python3.12/site-packages/multipart /usr/local/lib/python3.12/site-packages/multipart

FROM ${DATABASE_BASE} AS database-update
COPY database /workspace/database

FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package*.json ./
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm ci --no-audit --no-fund --registry=$NPM_REGISTRY
COPY frontend/ ./
RUN npm run typecheck && npm test && npm run build

FROM ${FRONTEND_BASE} AS frontend-update
COPY --from=frontend-build /app/dist /usr/share/nginx/html

FROM backend-update AS test-backend
RUN python -m pip install --no-cache-dir pytest ruff
