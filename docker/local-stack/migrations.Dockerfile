FROM node:22-bookworm-slim
WORKDIR /app
COPY api/package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY api/knexfile.js ./
COPY api/migrations/ ./migrations/
# The historical group migration checks this legacy directory before its SQL.
# Precreate it without copying the unrelated public assets into the migrator.
RUN mkdir -p /app/public/images/groups
ENV HOME=/tmp
CMD ["npm", "run", "migrate"]
