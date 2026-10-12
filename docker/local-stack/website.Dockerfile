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
RUN mkdir -p /app/admin /data
ENV ASPNETCORE_ENVIRONMENT=Production ASPNETCORE_URLS=http://0.0.0.0:5000 HOME=/tmp
EXPOSE 5000
ENTRYPOINT ["dotnet", "Roblox.Website.dll"]
