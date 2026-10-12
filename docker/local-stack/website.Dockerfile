# The staff panel is a separate Svelte bundle that the website serves from disk
# (Directories:AdminBundle). Without this build the image has no /admin/index.html
# and /admin/ fails for staff instead of rendering the panel.
FROM node:22-bookworm-slim AS admin
WORKDIR /admin
COPY admin/package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY admin/ ./
RUN npm run build

FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS build
WORKDIR /source
COPY Roblox/ Roblox/
RUN dotnet publish Roblox/Roblox.Website/Roblox.Website.csproj --configuration Release --output /publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim
RUN apt-get update && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /publish/ ./
COPY api/public/ /app/public/
COPY Roblox/Roblox.Libraries/Json/ /app/Json/
COPY Roblox/Roblox.Libraries/Templates/ /app/Templates/
COPY Roblox/Roblox.Libraries/StudioScripts/ /app/StudioScripts/
# The panel reads index.html, its favicons and build/bundle.{js,css} from here.
COPY --from=admin /admin/public/ /app/admin/
RUN mkdir -p /data
ENV ASPNETCORE_ENVIRONMENT=Production ASPNETCORE_URLS=http://0.0.0.0:5000 HOME=/tmp
EXPOSE 5000
ENTRYPOINT ["dotnet", "Roblox.Website.dll"]
