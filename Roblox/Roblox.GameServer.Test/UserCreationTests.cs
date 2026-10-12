using Roblox.Models.Users;
using Roblox.Rendering;
using Roblox.Services;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class UserCreationTests
{
    private readonly ReportingFixture fixture;
    public UserCreationTests(ReportingFixture fixture) { this.fixture = fixture; }

    [Fact]
    public async Task AnAccountHasSettingsEconomyAvatarAndSocialRowsWithoutARenderer()
    {
        RenderingHandler.Configure();
        using var users = new UsersService();
        var name = "Account" + Guid.NewGuid().ToString("N")[..10];
        var account = await users.CreateUser(name, "test-only-account-password", Gender.Unknown);
        Assert.Equal(1, await fixture.Scalar<int>("SELECT count(*)::int FROM user_settings WHERE user_id=:id", new {id=account.userId}));
        Assert.Equal(1, await fixture.Scalar<int>("SELECT count(*)::int FROM user_avatar WHERE user_id=:id", new {id=account.userId}));
        Assert.Equal(1, await fixture.Scalar<int>("SELECT count(*)::int FROM user_connections WHERE user_id=:id", new {id=account.userId}));
        Assert.Equal(100, await fixture.Scalar<long>("SELECT balance_robux FROM user_economy WHERE user_id=:id", new {id=account.userId}));
        Assert.True(await users.VerifyPassword(account.userId, "test-only-account-password"));
        Assert.False(await users.VerifyPassword(account.userId, "wrong-test-password"));
    }
}
