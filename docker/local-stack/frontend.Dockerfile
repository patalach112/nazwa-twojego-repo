FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
# Build with nonsecret placeholders. Actual private config is mounted at runtime.
RUN node -e "const fs=require('fs'); const c=JSON.parse(fs.readFileSync('config.example.json')); c.serverRuntimeConfig.backend={baseUrl:'http://website:5000'}; c.publicRuntimeConfig.backend.baseUrl='http://localhost:3000'; c.publicRuntimeConfig.backend.apiFormat='http://localhost:3000/apisite/{0}{1}'; c.publicRuntimeConfig.backend.proxyEnabled=true; fs.writeFileSync('config.json',JSON.stringify(c));"
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev --ignore-scripts

FROM node:22-bookworm-slim
WORKDIR /app
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules/ ./node_modules/
COPY --from=build /app/.next/ ./.next/
COPY --from=build /app/public/ ./public/
COPY --from=build /app/next.config.js ./
COPY --from=build /app/lib/backendProxy.cjs ./lib/backendProxy.cjs
# Only disposable Next cache is writable for the host user's runtime UID.
RUN mkdir -p .next/cache && chmod -R 777 .next/cache
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOME=/tmp
EXPOSE 3000
CMD ["node", "node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
