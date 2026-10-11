using Dapper;
using Roblox.Dto.Games;
using Roblox.Models.GameServer;

namespace Roblox.Services;

public partial class GameServerService
{
    private sealed class ReservedPorts
    {
        public int port { get; set; }
        public int? rccPort { get; set; }
        public int? proxyPort { get; set; }
    }

    private static int SelectUnusedPort(HashSet<int> ports, int start, int end)
    {
        var offset = Random.Shared.Next(end - start);
        for (var i = 0; i < end - start; i++)
        {
            var port = start + (offset + i) % (end - start);
            if (!ports.Contains(port)) return port;
        }
        throw new InvalidOperationException("No free game-server ports remain on this host");
    }

    private async Task<GameServerGetOrCreateResponse?> FindJoinableServer(PlaceEntry place, int matchmaking)
    {
        foreach (var server in await GetGameServersForPlace(place.placeId, matchmaking))
        {
            if (server.status == ServerStatus.Closing || server.updatedAt < DateTime.UtcNow.Subtract(HeartbeatTimeout)) continue;
            if ((await GetGameServerPlayers(server.id)).Count() >= place.maxPlayerCount) continue;
            return new GameServerGetOrCreateResponse
            {
                job = server.id, ip = Configuration.GameServerIp, port = server.port,
                status = server.status == ServerStatus.Ready ? JoinStatus.Joining : JoinStatus.Loading,
            };
        }
        return null;
    }

    public async Task<GameServerGetOrCreateResponse> GetServerForPlace(PlaceEntry placeInfo, int matchmaking)
    {
        if (placeInfo.placeId <= 0 || placeInfo.maxPlayerCount <= 0 || matchmaking <= 0)
            throw new ArgumentException("Invalid game-server launch options");
        await using var creationLock = await Cache.redLock.CreateLockAsync(
            $"CreateGameServerV2:{placeInfo.placeId}:{matchmaking}", TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(2), TimeSpan.FromMilliseconds(50));
        if (!creationLock.IsAcquired) return new GameServerGetOrCreateResponse { status = JoinStatus.Loading };
        var existing = await FindJoinableServer(placeInfo, matchmaking);
        if (existing != null) return existing;

        // Retry cleanup of stale/closing jobs before giving their ports to anyone.
        foreach (var server in await GetGameServersForPlace(placeInfo.placeId, matchmaking))
        {
            if (server.status != ServerStatus.Closing && server.updatedAt >= DateTime.UtcNow.Subtract(HeartbeatTimeout)) continue;
            await ShutDownServerAsync(server.id);
        }

        int rccPort = 0, networkPort = 0, proxyPort = 0;
        var newJob = false;
        var response = await InTransaction(async _ =>
        {
            // The negative key cannot collide with positive per-user report locks.
            // Allocation is global so different places cannot reserve the same port.
            await db.ExecuteAsync("SELECT pg_advisory_xact_lock(CAST(-2147483648 AS bigint))");
            var found = await FindJoinableServer(placeInfo, matchmaking);
            if (found != null) return found;
            var reserved = await db.QueryAsync<ReservedPorts>(
                "SELECT port, rcc_port AS rccPort, proxy_port AS proxyPort FROM asset_server WHERE ip = :ip", new { ip = Configuration.GameServerIp });
            var used = new HashSet<int>();
            foreach (var entry in reserved)
            {
                used.Add(entry.port);
                if (entry.rccPort.HasValue) used.Add(entry.rccPort.Value);
                if (entry.proxyPort.HasValue) used.Add(entry.proxyPort.Value);
            }
            rccPort = SelectUnusedPort(used, 30000, 40000);
            networkPort = SelectUnusedPort(used, 50000, 60000);
            proxyPort = SelectUnusedPort(used, 7000, 8000);
            var jobId = Guid.NewGuid();
            await db.ExecuteAsync(
                "INSERT INTO asset_server (id, asset_id, ip, port, rcc_port, proxy_port, server_connection, type, status) VALUES (:id, :asset, :ip, :port, :rcc, :proxy, :connection, :type, :status)",
                new { id = jobId, asset = placeInfo.placeId, ip = Configuration.GameServerIp, port = networkPort, rcc = rccPort, proxy = proxyPort,
                    connection = $"{Configuration.GameServerIp}:{networkPort}", type = matchmaking, status = ServerStatus.Loading });
            newJob = true;
            return new GameServerGetOrCreateResponse { job = jobId, ip = Configuration.GameServerIp, port = proxyPort, status = JoinStatus.Waiting };
        });
        if (!newJob) return response;
        try
        {
            // Reserve and commit before RPC: a fast Lua heartbeat can now find its job.
            await StartGameServer(placeInfo, rccPort, networkPort, proxyPort, response.job, matchmaking);
            return response; // spawned, NOT ready; only a heartbeat changes readiness
        }
        catch
        {
            try { await ShutDownServerAsync(response.job); }
            catch (Exception cleanupError)
            {
                // Keep the closing reservation when remote termination is uncertain.
                Console.Error.WriteLine($"Could not clean up failed game launch {response.job}: {cleanupError.GetType().Name}");
            }
            throw;
        }
    }

    public async Task<string> StartGameServer(PlaceEntry placeInfo, int rccPort, int networkServerPort, int proxyPort, Guid jobId, int matchmaking)
    {
        var request = ArbiterHttpClient.CreateGameServerRequest(placeInfo, rccPort, networkServerPort, proxyPort, jobId, matchmaking);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        await arbiterClient.StartGameServer(request, cancellation.Token);
        return "OK";
    }

    public async Task ShutDownServerAsync(Guid serverId)
    {
        // An unsuccessful kill must not make a still-running job look ready or
        // free its ports. Callers can retry a Closing job once the arbiter recovers.
        await db.ExecuteAsync("UPDATE asset_server SET status = :closing WHERE id = :id", new { id = serverId, closing = ServerStatus.Closing });
        using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        await arbiterClient.KillGameServer(ArbiterHttpClient.CreateKillGameServerRequest(serverId), cancellation.Token);
        await DeleteGameServer(serverId);
    }
}
