using Microsoft.Extensions.Configuration;
using Roblox.Dto.Games;
using Roblox.Models.GameServer;
using Roblox.Libraries.EasyJwt;
using Roblox.Services;
using Roblox.Website.Lib;
using Roblox.Website.Middleware;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class JwtConfigurationTests
{
    [Fact]
    public void GameTicketsReallyUseTheConfiguredKey()
    {
        var key = new string('a', 64);
        var other = new string('b', 64);
        using var service = new GameServerService();
        GameServerService.Configure(key);
        var ticket = service.CreateGameServerTicket(123, "http://example.test/game");
        Assert.Equal(123, service.DecodeGameServerTicket(ticket).placeId);
        Assert.Equal(123, new EasyJwt().DecodeJwt<GameServerTicketJwt>(ticket, key).placeId);
        Assert.ThrowsAny<Exception>(() => new EasyJwt().DecodeJwt<GameServerTicketJwt>(ticket, other));
        GameServerService.Configure(other);
        Assert.ThrowsAny<Exception>(() => service.DecodeGameServerTicket(ticket));
        Assert.Throws<ArgumentException>(() => GameServerService.Configure(""));
    }

    [Fact]
    public void SessionCookiesUseTheirOwnConfiguredKey()
    {
        var key = new string('c', 64);
        SessionMiddleware.Configure(key);
        var token = SessionMiddleware.CreateJwt(new JwtEntry { sessionId = "test-session", createdAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() });
        Assert.Equal("test-session", SessionMiddleware.DecodeJwt<JwtEntry>(token).sessionId);
        Assert.Equal("test-session", new EasyJwt().DecodeJwt<JwtEntry>(token, key).sessionId);
        Assert.ThrowsAny<Exception>(() => new EasyJwt().DecodeJwt<JwtEntry>(token, new string('d', 64)));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("change-me")]
    [InlineData("<SECRET>")]
    public void MissingAndExampleSecretsFailWithAnActionableMessage(string? value)
    {
        var config = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["Jwt:Sessions"] = value }).Build();
        var exception = Assert.Throws<InvalidOperationException>(() => ConfigurationSecrets.Require(config, "Jwt:Sessions"));
        Assert.Contains("local-secrets.json", exception.Message);
        if (!string.IsNullOrEmpty(value)) Assert.DoesNotContain(value, exception.Message);
    }
}
