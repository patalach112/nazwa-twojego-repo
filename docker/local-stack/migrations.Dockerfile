FROM node:22-bookworm-slim
WORKDIR /app
COPY api/package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY api/knexfile.js ./
COPY api/migrations/ ./migrations/
ENV HOME=/tmp
CMD ["npm", "run", "migrate"]
