using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Roblox.Services;
using Roblox.Website.Controllers;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class NativePresenceTests
{
    private readonly ReportingFixture db;
    private const string RccSecret = "test-native-rcc-authorization";
    public NativePresenceTests(ReportingFixture db) { this.db = db; }

    private static BypassController NativeController(Guid job, long place)
    {
        Configuration.RccAuthorization = RccSecret;
        Configuration.DiscordBotToken = "unused-test-discord-token";
        var context = new DefaultHttpContext();
        context.Request.Headers["accesskey"] = RccSecret;
        context.Request.Headers["Roblox-Game-Id"] = job.ToString();
        context.Request.Headers["Roblox-Place-Id"] = place.ToString();
        return new BypassController { ControllerContext = new ControllerContext { HttpContext = context } };
    }

    [Fact]
    public async Task AFormerRccCannotRemoveTheUsersCurrentJob()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var oldJob = await db.Server(place);
        var newJob = await db.Server(place);
        using (var service = new GameServerService())
        {
            await service.OnPlayerJoin(user, place, oldJob);
            await service.OnPlayerJoin(user, place, newJob);
        }
        await NativeController(oldJob, place).RegisterGamePresenceAbsence(user);
        Assert.Equal(newJob, await db.Scalar<Guid>("SELECT server_id FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(1L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE server_id = :id AND ended_at IS NULL", new { id = newJob }));
        Assert.Equal(place, GameServerService.GetUserPlaceId(user));
    }

    [Fact]
    public async Task NativeAbsenceDoesNotNeedAProcessLocalPlaceCache()
    {
        var user = await db.User();
        var place = await db.Place(user);
        var job = await db.Server(place);
        using (var service = new GameServerService()) await service.OnPlayerJoin(user, place, job);
        GameServerService.CurrentPlayersInGame.TryRemove(user, out _);
        await NativeController(job, place).RegisterGamePresenceAbsence(user);
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_server_player WHERE user_id = :id", new { id = user }));
        Assert.Equal(0L, await db.Scalar<long>("SELECT COUNT(*) FROM asset_play_history WHERE user_id = :id AND ended_at IS NULL", new { id = user }));
    }

    [Fact]
    public async Task MissingSecretsAndJobHeadersFailClosed()
    {
        var controller = NativeController(Guid.NewGuid(), 1);
        controller.Request.Headers.Remove("accesskey");
        var configured = Configuration.RccAuthorization;
        try
        {
            Configuration.RccAuthorization = null!;
            await Assert.ThrowsAsync<UnauthorizedAccessException>(() => controller.RegisterGamePresenceAbsence(1));
            Configuration.RccAuthorization = configured;
            controller.Request.Headers["accesskey"] = configured;
            controller.Request.Headers.Remove("Roblox-Game-Id");
            await Assert.ThrowsAsync<UnauthorizedAccessException>(() => controller.RegisterGamePresenceAbsence(1));
        }
        finally { Configuration.RccAuthorization = configured; }
    }
}
