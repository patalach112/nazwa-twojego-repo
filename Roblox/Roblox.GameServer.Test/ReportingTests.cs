using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Roblox.Dto.Games;
using Roblox.Models.Assets;
using Roblox.Models.Economy;
using Roblox.Services;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class ReportingTests
{
    private readonly ReportingFixture db;
    public ReportingTests(ReportingFixture db) { this.db = db; }

    private static Dictionary<string, object?> Report(Guid server, long place, long user, string eventType = "Join") => new()
    {
        ["authorization"] = ReportingFixture.Secret, ["serverId"] = server,
        ["placeId"] = place.ToString(), ["userId"] = user.ToString(), ["eventType"] = eventType,
    };

    private async Task<JsonElement> Post(string path, object payload, HttpStatusCode expected = HttpStatusCode.OK)
    {
        using var response = await db.Client.PostAsJsonAsync(path, payload);
        var body = await response.Content.ReadAsStringAsync();
        Assert.True(response.StatusCode == expected, $"{path} returned {response.StatusCode}, expected {expected}: {body}");
        using var json = JsonDocument.Parse(body);
        return json.RootElement.Clone();
    }

    [Fact]
    public async Task EveryRouteRequiresTheGameSecretEvenWithRobloxHeaders()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        foreach (var route in new[] { "/gs/ping", "/gs/players/report", "/gs/activity", "/gs/shutdown", "/gs/delete" })
        {
            foreach (var secret in new string?[] { null, "", "wrong", ReportingFixture.ArbiterSecret })
            foreach (var userAgent in new string?[] { null, "Roblox/WinInet", "axios/1.9.0", "node-fetch/3.3" })
            {
                var payload = Report(server, place, user);
                payload["authorization"] = secret;
                using var request = new HttpRequestMessage(HttpMethod.Post, route) { Content = JsonContent.Create(payload) };
                if (userAgent != null) request.Headers.Add("User-Agent", userAgent);
                request.Headers.Add("accesskey", ReportingFixture.Secret);
                using var response = await db.Client.SendAsync(request);
                Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
                Assert.DoesNotContain(ReportingFixture.Secret, await response.Content.ReadAsStringAsync());
            }
        }
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id", new { id = server }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE server_id = :id", new { id = server }));
    }

    [Fact]
    public async Task MalformedReportsAreRejectedBeforeTheyChangeState()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        foreach (var (field, value) in new (string, object?)[] { ("serverId", "not-a-uuid"), ("serverId", Guid.Empty), ("placeId", "0"), ("userId", "-1"), ("userId", "1.5"), ("eventType", "Kick"), ("eventType", null) })
        {
            var payload = Report(server, place, user);
            payload[field] = value;
            await Post("/gs/players/report", payload, HttpStatusCode.BadRequest);
        }
        using var malformed = await db.Client.PostAsync("/gs/ping", new StringContent("{", Encoding.UTF8, "application/json"));
        Assert.Equal(HttpStatusCode.BadRequest, malformed.StatusCode);
        using var text = await db.Client.PostAsync("/gs/ping", new StringContent("{}", Encoding.UTF8, "text/plain"));
        Assert.Equal(HttpStatusCode.UnsupportedMediaType, text.StatusCode);
        Assert.Equal(0L, await db.Scalar<long>("SELECT visit_count FROM asset_place WHERE asset_id = :id", new { id = place }));
    }

    [Fact]
    public async Task HeartbeatsMakeTheJobReadyAndUpdateOptionalStatistics()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        var identity = new { authorization = ReportingFixture.Secret, serverId = server };
        Assert.False((await Post("/gs/activity", identity)).GetProperty("isAlive").GetBoolean());
        await Post("/gs/ping", new { authorization = ReportingFixture.Secret, serverId = server, placeId = place, ping = 14.5m, fps = 59.8m });
        Assert.True((await Post("/gs/activity", identity)).GetProperty("isAlive").GetBoolean());
        Assert.Equal((int)ServerStatus.Ready, await db.Scalar<int>("SELECT status FROM asset_server WHERE id = :id", new { id = server }));
        Assert.Equal(14L, await db.Scalar<long>("SELECT ping FROM asset_server WHERE id = :id", new { id = server }));
        Assert.Equal(60L, await db.Scalar<long>("SELECT fps FROM asset_server WHERE id = :id", new { id = server }));
        await Post("/gs/ping", new { authorization = ReportingFixture.Secret, serverId = server, placeId = place.ToString() });
        Assert.Equal(14L, await db.Scalar<long>("SELECT ping FROM asset_server WHERE id = :id", new { id = server }));
    }

    [Fact]
    public async Task UnknownStaleAndClosingJobsAreNotAlive()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        await db.Execute("UPDATE asset_server SET status = 2, updated_at = :old WHERE id = :id", new { id = server, old = DateTime.UtcNow.Subtract(TimeSpan.FromMinutes(10)) });
        Assert.False((await Post("/gs/activity", new { authorization = ReportingFixture.Secret, serverId = server })).GetProperty("isAlive").GetBoolean());
        var unknown = await Post("/gs/activity", new { authorization = ReportingFixture.Secret, serverId = Guid.NewGuid() });
        Assert.False(unknown.GetProperty("isAlive").GetBoolean());
        Assert.Equal(JsonValueKind.Null, unknown.GetProperty("updatedAt").ValueKind);
        await db.Execute("UPDATE asset_server SET status = 3, updated_at = now() WHERE id = :id", new { id = server });
        await Post("/gs/ping", new { authorization = ReportingFixture.Secret, serverId = server, placeId = place }, HttpStatusCode.NotFound);
        await Post("/gs/players/report", Report(server, place, user), HttpStatusCode.Conflict);
        Assert.False((await Post("/gs/activity", new { authorization = ReportingFixture.Secret, serverId = server })).GetProperty("isAlive").GetBoolean());
    }

    [Fact]
    public async Task AReportCannotRegisterAnUnknownUserOrChangeAnotherPlace()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        await Post("/gs/players/report", Report(server, place, long.MaxValue), HttpStatusCode.NotFound);
        await Post("/gs/players/report", Report(Guid.NewGuid(), place, user), HttpStatusCode.NotFound);
        await Post("/gs/players/report", Report(server, place + 999999, user), HttpStatusCode.Conflict);
        await Post("/gs/ping", new { authorization = ReportingFixture.Secret, serverId = server, placeId = place + 999999 }, HttpStatusCode.NotFound);
        await Post("/gs/shutdown", new { authorization = ReportingFixture.Secret, serverId = server, placeId = place + 999999 }, HttpStatusCode.Conflict);
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id", new { id = server }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT visit_count FROM asset_place WHERE asset_id = :id", new { id = place }));
    }

    [Fact]
    public async Task ConcurrentJoinRetriesCreateOneMembershipVisitHistoryAndCreatorReward()
    {
        var creator = await db.User();
        var user = await db.User();
        var place = await db.Place(creator);
        var server = await db.Server(place);
        await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => Post("/gs/players/report", Report(server, place, user))));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id AND server_id = :server", new { id = user, server }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT visit_count FROM asset_place WHERE asset_id = :id", new { id = place }));
        Assert.Equal(10L, await db.Scalar<long>("SELECT balance_tickets FROM user_economy WHERE user_id = :id", new { id = creator }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM user_transaction WHERE asset_id = :id AND type = :type", new { id = place, type = PurchaseType.PlaceVisit }));
        Assert.Equal(place, GameServerService.GetUserPlaceId(user));
    }

    [Fact]
    public async Task AGroupActuallyReceivesItsVisitReward()
    {
        var user = await db.User();
        var group = user + 1000000;
        await db.Execute("INSERT INTO group_economy (group_id, balance_robux, balance_tickets) VALUES (:id, 0, 0)", new { id = group });
        var place = await db.Place(group, CreatorType.Group);
        var server = await db.Server(place);
        await Post("/gs/players/report", Report(server, place, user));
        await Post("/gs/players/report", Report(server, place, user));
        Assert.Equal(10L, await db.Scalar<long>("SELECT balance_tickets FROM group_economy WHERE group_id = :id", new { id = group }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM user_transaction WHERE asset_id = :id AND group_id_one = :group AND user_id_one IS NULL", new { id = place, group }));
    }

    [Fact]
    public async Task FailedRewardsRollBackTheWholeJoinInsteadOfLeavingAHalfVisit()
    {
        var creator = await db.User();
        var user = await db.User();
        var place = await db.Place(creator);
        var server = await db.Server(place);
        await db.Execute("DELETE FROM user_economy WHERE user_id = :id", new { id = creator });
        using var service = new GameServerService();
        await Assert.ThrowsAnyAsync<Exception>(() => service.OnPlayerJoin(user, place, server));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id", new { id = user }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT visit_count FROM asset_place WHERE asset_id = :id", new { id = place }));
        Assert.Equal(0L, GameServerService.GetUserPlaceId(user));
    }

    [Fact]
    public async Task DuplicateLeaveClosesTheSessionAndPaysOnlyOnceWithinTheBudget()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        await Post("/gs/players/report", Report(server, place, user));
        await db.Execute("UPDATE asset_play_history SET created_at = :start WHERE server_id = :id", new { id = server, start = DateTime.UtcNow.Subtract(TimeSpan.FromMinutes(2)) });
        await db.Execute("INSERT INTO user_transaction (type, currency_type, amount, user_id_one, user_id_two) VALUES (100, 2, 9990, :id, 1)", new { id = user });
        await Task.WhenAll(Enumerable.Range(0, 4).Select(_ => Post("/gs/players/report", Report(server, place, user, "Leave"))));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id AND ended_at IS NULL", new { id = user }));
        Assert.Equal(10L, await db.Scalar<long>("SELECT balance_tickets FROM user_economy WHERE user_id = :id", new { id = user }));
        Assert.Equal(10000L, await db.Scalar<long>("SELECT SUM(amount) FROM user_transaction WHERE user_id_one = :id AND type = 100", new { id = user }));
        Assert.Equal(0L, GameServerService.GetUserPlaceId(user));
    }

    [Fact]
    public async Task AStrayLeaveBeforeJoinCannotCreateHistoryOrCurrency()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var server = await db.Server(place);
        await Post("/gs/players/report", Report(server, place, user, "Leave"));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id", new { id = user }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT balance_tickets FROM user_economy WHERE user_id = :id", new { id = user }));
    }

    [Fact]
    public async Task ALateLeaveFromTheOldJobCannotEndTheNewSessionInTheSamePlace()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var oldJob = await db.Server(place);
        var newJob = await db.Server(place);
        await Post("/gs/players/report", Report(oldJob, place, user));
        await Post("/gs/players/report", Report(newJob, place, user));
        await Post("/gs/players/report", Report(oldJob, place, user, "Leave"));
        await Post("/gs/delete", new { authorization = ReportingFixture.Secret, serverId = oldJob });
        Assert.Equal(newJob, await db.Scalar<Guid>("SELECT server_id FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id AND server_id = :server AND ended_at IS NULL", new { id = user, server = newJob }));
        Assert.Equal(place, GameServerService.GetUserPlaceId(user));
    }

    [Theory]
    [InlineData("/gs/shutdown")]
    [InlineData("/gs/delete")]
    public async Task ShutdownNotificationsCloseOnlyTheirOwnJobAndAreSafeToRetry(string route)
    {
        var first = await db.User();
        var second = await db.User();
        var place = await db.Place(first);
        var firstJob = await db.Server(place);
        var secondJob = await db.Server(place);
        await Post("/gs/players/report", Report(firstJob, place, first));
        await Post("/gs/players/report", Report(secondJob, place, second));
        var payload = new { authorization = ReportingFixture.Secret, serverId = firstJob, placeId = place };
        await Post(route, payload);
        await Post(route, payload);
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id", new { id = firstJob }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE server_id = :id", new { id = firstJob }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE server_id = :id AND ended_at IS NULL", new { id = firstJob }));
        Assert.Equal(0L, GameServerService.GetUserPlaceId(first));
        Assert.Equal(place, GameServerService.GetUserPlaceId(second));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE server_id = :id", new { id = secondJob }));
    }

    [Fact]
    public async Task RacingJoinAndDeleteCannotLeaveAnOrphanOrCachedPlayer()
    {
        for (var i = 0; i < 8; i++)
        {
            var user = await db.User();
            var place = await db.Place(user);
            var server = await db.Server(place);
            var join = db.Client.PostAsJsonAsync("/gs/players/report", Report(server, place, user));
            var deletion = db.Client.PostAsJsonAsync("/gs/delete", new { authorization = ReportingFixture.Secret, serverId = server });
            using var joined = await join;
            using var deleted = await deletion;
            Assert.True(joined.StatusCode is HttpStatusCode.OK or HttpStatusCode.NotFound);
            Assert.Equal(HttpStatusCode.OK, deleted.StatusCode);
            Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE user_id = :id", new { id = user }));
            Assert.Equal(0L, GameServerService.GetUserPlaceId(user));
        }
    }
}
