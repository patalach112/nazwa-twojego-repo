using System.Collections.Concurrent;
using Dapper;
using Roblox.Dto.Games;
using Roblox.Models.Assets;
using Roblox.Models.Economy;
using Roblox.Services.Exceptions;

namespace Roblox.Services;

public partial class GameServerService
{
    public static readonly TimeSpan HeartbeatTimeout = TimeSpan.FromMinutes(5);
    // Bounded striped locks keep this process's presence cache in commit order.
    // PostgreSQL advisory locks also serialize the same user across website instances.
    private static readonly SemaphoreSlim[] PlayerReportLocks = Enumerable.Range(0, 128).Select(_ => new SemaphoreSlim(1, 1)).ToArray();
    private static readonly ConcurrentDictionary<long, Guid> PlayerJobs = new();
    private static readonly object PlayerPresenceLock = new();

    private async Task WithPlayerReportLock(long userId, Func<Task> operation)
    {
        var gate = PlayerReportLocks[(int)(userId % PlayerReportLocks.Length)];
        await gate.WaitAsync();
        try { await operation(); }
        finally { gate.Release(); }
    }

    private static void TrackPlayer(long userId, long placeId, Guid serverId)
    {
        lock (PlayerPresenceLock)
        {
            PlayerJobs[userId] = serverId;
            CurrentPlayersInGame[userId] = placeId;
        }
    }

    private static void ForgetPlayer(long userId, Guid serverId)
    {
        lock (PlayerPresenceLock)
        {
            if (!PlayerJobs.TryGetValue(userId, out var currentJob) || currentJob != serverId) return;
            PlayerJobs.TryRemove(userId, out _);
            CurrentPlayersInGame.TryRemove(userId, out _);
        }
    }

    private async Task<GameServerDb> LockReportingServer(Guid serverId, long? placeId = null)
    {
        var server = await db.QuerySingleOrDefaultAsync<GameServerDb>(
            "SELECT id, asset_id AS assetId, status, updated_at AS updatedAt FROM asset_server WHERE id = :id FOR UPDATE",
            new { id = serverId });
        if (server == null) throw new RecordNotFoundException("Unknown game server");
        if (placeId.HasValue && server.assetId != placeId.Value) throw new ArgumentException("The place does not match this game server");
        return server;
    }

    public async Task OnPlayerJoin(long userId, long placeId, Guid serverId)
    {
        if (userId <= 0 || placeId <= 0 || serverId == Guid.Empty) throw new ArgumentException("Invalid player report IDs");
        await WithPlayerReportLock(userId, async () =>
        {
            await InTransaction(async _ =>
            {
                await db.ExecuteAsync("SELECT pg_advisory_xact_lock(:user_id)", new { user_id = userId });
                var server = await LockReportingServer(serverId, placeId);
                if (server.status == ServerStatus.Closing) throw new InvalidOperationException("This game server is closing");
                if (!await db.QuerySingleAsync<bool>("SELECT EXISTS (SELECT 1 FROM \"user\" WHERE id = :id)", new { id = userId }))
                    throw new RecordNotFoundException("Unknown player");
                var previous = await db.QuerySingleOrDefaultAsync<GameServerEntry>(
                    "SELECT server_id AS id, asset_id AS assetId FROM asset_server_player WHERE user_id = :user_id FOR UPDATE",
                    new { user_id = userId });
                if (previous?.id == serverId) return 0; // retry: no second visit, history or reward
                if (previous != null)
                {
                    await db.ExecuteAsync("DELETE FROM asset_server_player WHERE user_id = :user_id", new { user_id = userId });
                    await FinishPlaySession(userId, previous.assetId, previous.id);
                }
                await db.ExecuteAsync(
                    "INSERT INTO asset_server_player (asset_id, user_id, server_id) VALUES (:asset_id, :user_id, :server_id)",
                    new { asset_id = placeId, user_id = userId, server_id = serverId });
                await InsertAsync("asset_play_history", new { asset_id = placeId, user_id = userId, server_id = serverId });
                var changed = await db.ExecuteAsync("UPDATE asset_place SET visit_count = visit_count + 1 WHERE asset_id = :id", new { id = placeId });
                if (changed != 1) throw new RecordNotFoundException("Unknown place");
                await GrantCreatorVisitReward(userId, placeId);
                return 0;
            });
            TrackPlayer(userId, placeId, serverId);
        });
    }

    public async Task OnPlayerLeave(long userId, long placeId, Guid serverId)
    {
        if (userId <= 0 || placeId <= 0 || serverId == Guid.Empty) throw new ArgumentException("Invalid player report IDs");
        await WithPlayerReportLock(userId, async () =>
        {
            await InTransaction(async _ =>
            {
                await db.ExecuteAsync("SELECT pg_advisory_xact_lock(:user_id)", new { user_id = userId });
                await LockReportingServer(serverId, placeId);
                var deleted = await db.ExecuteAsync(
                    "DELETE FROM asset_server_player WHERE user_id = :user_id AND server_id = :server_id",
                    new { user_id = userId, server_id = serverId });
                if (deleted != 0) await FinishPlaySession(userId, placeId, serverId);
                return 0;
            });
            // A late Leave from a former job must not clear the user's new presence.
            ForgetPlayer(userId, serverId);
        });
    }

    private sealed class PlaceCreator
    {
        public CreatorType creatorType { get; set; }
        public long creatorId { get; set; }
    }

    private async Task GrantCreatorVisitReward(long userId, long placeId)
    {
        var creator = await db.QuerySingleOrDefaultAsync<PlaceCreator>(
            "SELECT creator_type AS creatorType, creator_id AS creatorId FROM asset WHERE id = :id", new { id = placeId });
        if (creator == null) throw new RecordNotFoundException("Unknown place asset");
        if (creator.creatorType == CreatorType.User && creator.creatorId == userId) return;
        using var cooldown = ServiceProvider.GetOrCreate<CooldownService>(this);
        // Preserve the existing limit: at most 100 rewarded visits per place per day.
        if (!await cooldown.TryIncrementBucketCooldown("TicketCreatorPlaceVisit:" + placeId, 100, TimeSpan.FromDays(1))) return;
        using var economy = ServiceProvider.GetOrCreate<EconomyService>(this);
        await economy.IncrementCurrency(creator.creatorType, creator.creatorId, CurrencyType.Tickets, 10);
        await InsertAsync("user_transaction", new
        {
            amount = 10,
            currency_type = CurrencyType.Tickets,
            user_id_one = creator.creatorType == CreatorType.User ? (long?)creator.creatorId : null,
            user_id_two = userId,
            group_id_one = creator.creatorType == CreatorType.Group ? (long?)creator.creatorId : null,
            type = PurchaseType.PlaceVisit,
            asset_id = placeId,
        });
        if (creator.creatorType != CreatorType.User) return;
        var visits = await db.QuerySingleAsync<long>(
            "SELECT COALESCE(SUM(p.visit_count), 0) FROM asset_place p INNER JOIN asset a ON a.id = p.asset_id WHERE a.creator_type = :type AND a.creator_id = :id",
            new { type = CreatorType.User, id = creator.creatorId });
        foreach (var (threshold, badgeId) in new[] { (100L, 6), (1000L, 7) })
        {
            if (visits < threshold) continue;
            await db.ExecuteAsync(
                "INSERT INTO user_badge (user_id, badge_id) SELECT :user_id, :badge_id WHERE NOT EXISTS (SELECT 1 FROM user_badge WHERE user_id = :user_id AND badge_id = :badge_id)",
                new { user_id = creator.creatorId, badge_id = badgeId });
        }
    }

    private async Task FinishPlaySession(long userId, long placeId, Guid serverId)
    {
        var ended = await db.QueryAsync<AssetPlayEntry>(
            "UPDATE asset_play_history SET ended_at = now() WHERE user_id = :user_id AND asset_id = :asset_id AND ended_at IS NULL AND (server_id = :server_id OR server_id IS NULL) RETURNING id, created_at AS createdAt",
            new { user_id = userId, asset_id = placeId, server_id = serverId });
        var session = ended.OrderByDescending(entry => entry.id).FirstOrDefault();
        if (session == null) return;
        var minutes = (long)Math.Floor(Math.Max(0, (DateTime.UtcNow - session.createdAt.ToUniversalTime()).TotalMinutes));
        var reward = Math.Min(minutes * 10, 60);
        if (reward == 0) return;
        const long maxEarningsPerPeriod = 10000;
        // '= NULL' never matches in SQL; the old helper effectively disabled this cap.
        var earned = await db.QuerySingleAsync<long>(
            "SELECT COALESCE(SUM(amount), 0) FROM user_transaction WHERE user_id_one = :user_id AND type = :type AND currency_type = :currency AND created_at >= :since",
            new { user_id = userId, type = PurchaseType.PlayingGame, currency = CurrencyType.Tickets, since = DateTime.UtcNow.Subtract(TimeSpan.FromHours(12)) });
        reward = Math.Min(reward, Math.Max(0, maxEarningsPerPeriod - earned));
        if (reward == 0) return;
        using var economy = ServiceProvider.GetOrCreate<EconomyService>(this);
        await economy.IncrementCurrency(CreatorType.User, userId, CurrencyType.Tickets, reward);
        await InsertAsync("user_transaction", new
        {
            amount = reward, currency_type = CurrencyType.Tickets, user_id_one = userId,
            user_id_two = 1L, type = PurchaseType.PlayingGame, asset_id = placeId,
        });
    }

    public async Task SetServerPing(Guid serverId, long? placeId = null, long? ping = null, long? fps = null)
    {
        var changed = await db.ExecuteAsync(
            "UPDATE asset_server SET updated_at = now(), status = :ready, ping = COALESCE(:ping, ping), fps = COALESCE(:fps, fps) WHERE id = :id AND (CAST(:place_id AS bigint) IS NULL OR asset_id = :place_id) AND status <> :closing",
            new { id = serverId, place_id = placeId, ready = ServerStatus.Ready, closing = ServerStatus.Closing, ping, fps });
        if (changed == 0) throw new RecordNotFoundException("Unknown or closing game server for this place");
    }

    public async Task<GameServerActivity> GetServerActivity(Guid serverId)
    {
        var server = await GetGameServer(serverId);
        return new GameServerActivity
        {
            isAlive = server != null && server.status == ServerStatus.Ready && server.updatedAt >= DateTime.UtcNow.Subtract(HeartbeatTimeout),
            updatedAt = server?.updatedAt,
        };
    }

    public async Task DeleteGameServer(Guid serverId, long? placeId = null)
    {
        var players = await InTransaction(async _ =>
        {
            var server = await db.QuerySingleOrDefaultAsync<GameServerDb>(
                "SELECT id, asset_id AS assetId FROM asset_server WHERE id = :id FOR UPDATE", new { id = serverId });
            if (server == null) return new List<long>(); // idempotent notification
            if (placeId.HasValue && server.assetId != placeId.Value) throw new ArgumentException("The place does not match this game server");
            var removed = (await db.QueryAsync<long>(
                "DELETE FROM asset_server_player WHERE server_id = :id RETURNING user_id", new { id = serverId })).ToList();
            foreach (var userId in removed) await FinishPlaySession(userId, server.assetId, serverId);
            // Also close orphaned history from interrupted legacy Leave reports.
            await db.ExecuteAsync("UPDATE asset_play_history SET ended_at = now() WHERE server_id = :id AND ended_at IS NULL", new { id = serverId });
            await db.ExecuteAsync("DELETE FROM asset_server WHERE id = :id", new { id = serverId });
            return removed;
        });
        // Wait for any join that committed just before deletion to update its cache.
        foreach (var userId in players) await WithPlayerReportLock(userId, () =>
        {
            ForgetPlayer(userId, serverId);
            return Task.CompletedTask;
        });
    }
}
