using System.Net;
using System.Text.Json;
using Roblox.Dto.Games;
using Roblox.Models.GameServer;
using Roblox.Services;
using Roblox.Services.Exceptions;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class LaunchTests
{
    private readonly ReportingFixture db;
    public LaunchTests(ReportingFixture db) { this.db = db; }

    private sealed class ArbiterHandler : HttpMessageHandler
    {
        public Func<string, JsonElement, Task<HttpStatusCode>> Reply { get; set; } = null!;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.Equal(ReportingFixture.ArbiterSecret, request.Headers.GetValues("PJX-ArbiterAUTH").Single());
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellationToken));
            var status = await Reply(request.RequestUri!.AbsolutePath, body.RootElement);
            return new HttpResponseMessage(status) { Content = new StringContent("{}") };
        }
    }

    private async Task<PlaceEntry> Place()
    {
        var user = await db.User();
        var place = await db.Place(user);
        return new PlaceEntry { placeId = place, universeId = place, builderId = user, maxPlayerCount = 10, year = 2016 };
    }

    [Fact]
    public async Task ReservationCommitsBeforeSpawnAndTheProxyPortSurvivesReadiness()
    {
        var place = await Place();
        Guid launched = Guid.Empty;
        var starts = 0;
        var proxy = 0;
        using var handler = new ArbiterHandler { Reply = async (path, request) =>
        {
            Assert.Equal("/start-game-server", path);
            starts++;
            launched = request.GetProperty("jobId").GetGuid();
            proxy = request.GetProperty("proxyPort").GetInt32();
            Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id AND status = 1", new { id = launched }));
            Assert.Equal(proxy, await db.Scalar<int>("SELECT proxy_port FROM asset_server WHERE id = :id", new { id = launched }));
            Assert.Equal(request.GetProperty("rccPort").GetInt32(), await db.Scalar<int>("SELECT rcc_port FROM asset_server WHERE id = :id", new { id = launched }));
            Assert.Equal(request.GetProperty("gameServerPort").GetInt32(), await db.Scalar<int>("SELECT port FROM asset_server WHERE id = :id", new { id = launched }));
            return HttpStatusCode.OK;
        } };
        using var client = new GameServerService.ArbiterHttpClient(handler);
        using var service = new GameServerService(client);
        var created = await service.GetServerForPlace(place, 1);
        Assert.Equal(JoinStatus.Waiting, created.status);
        Assert.Equal(launched, created.job);
        Assert.Equal(proxy, created.port);
        var loading = await service.GetServerForPlace(place, 1);
        Assert.Equal(JoinStatus.Loading, loading.status);
        Assert.Equal(created.job, loading.job);
        await service.SetServerPing(created.job, place.placeId);
        var ready = await service.GetServerForPlace(place, 1);
        Assert.Equal(JoinStatus.Joining, ready.status);
        Assert.Equal(proxy, ready.port);
        Assert.Equal(1, starts);
    }

    [Fact]
    public async Task ARejectedLaunchIsCleanedUpWhenTheArbiterConfirmsTermination()
    {
        var place = await Place();
        Guid launched = Guid.Empty;
        var kills = 0;
        using var handler = new ArbiterHandler { Reply = async (path, request) =>
        {
            var job = request.GetProperty("jobId").GetGuid();
            if (path == "/start-game-server") { launched = job; return HttpStatusCode.BadGateway; }
            Assert.Equal("/kill-game-server", path);
            kills++;
            Assert.Equal((int)ServerStatus.Closing, await db.Scalar<int>("SELECT status FROM asset_server WHERE id = :id", new { id = job }));
            return HttpStatusCode.OK;
        } };
        using var client = new GameServerService.ArbiterHttpClient(handler);
        using var service = new GameServerService(client);
        await Assert.ThrowsAsync<HttpRequestException>(() => service.GetServerForPlace(place, 1));
        Assert.Equal(1, kills);
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id", new { id = launched }));
    }

    [Fact]
    public async Task FailedTerminationKeepsAClosingReservationAndCanBeRetried()
    {
        var place = await Place();
        Guid launched = Guid.Empty;
        var allowKill = false;
        using var handler = new ArbiterHandler { Reply = (path, request) =>
        {
            launched = request.GetProperty("jobId").GetGuid();
            return Task.FromResult(path == "/kill-game-server" && allowKill ? HttpStatusCode.OK : HttpStatusCode.BadGateway);
        } };
        using var client = new GameServerService.ArbiterHttpClient(handler);
        using var service = new GameServerService(client);
        await Assert.ThrowsAsync<HttpRequestException>(() => service.GetServerForPlace(place, 1));
        Assert.Equal((int)ServerStatus.Closing, await db.Scalar<int>("SELECT status FROM asset_server WHERE id = :id", new { id = launched }));
        await Assert.ThrowsAsync<RecordNotFoundException>(() => service.SetServerPing(launched, place.placeId));
        allowKill = true;
        await service.ShutDownServerAsync(launched);
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE id = :id", new { id = launched }));
    }

    [Fact]
    public async Task ConcurrentLaunchesForTheSamePlaceReuseOnePendingJob()
    {
        var place = await Place();
        var starts = 0;
        using var handler = new ArbiterHandler { Reply = (_, _) => { Interlocked.Increment(ref starts); return Task.FromResult(HttpStatusCode.OK); } };
        using var client = new GameServerService.ArbiterHttpClient(handler);
        var results = await Task.WhenAll(Enumerable.Range(0, 6).Select(async _ =>
        {
            using var service = new GameServerService(client);
            return await service.GetServerForPlace(place, 1);
        }));
        Assert.Equal(1, starts);
        Assert.Single(results.Select(result => result.job).Distinct());
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server WHERE asset_id = :id", new { id = place.placeId }));
    }
}
