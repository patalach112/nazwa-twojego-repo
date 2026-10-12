using System.Reflection;
using Dapper;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.ApplicationParts;
using Microsoft.AspNetCore.Mvc.Controllers;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;
using Roblox.Models.Assets;
using Roblox.Services;
using Roblox.Website.Controllers;
using Roblox.Website.Middleware;
using Xunit;

namespace Roblox.GameServer.Test;

[CollectionDefinition("Reporting", DisableParallelization = true)]
public class ReportingCollection : ICollectionFixture<ReportingFixture> { }

public sealed class ReportingFixture : IAsyncLifetime
{
    public const string Secret = "test-game-server-authorization";
    public const string ArbiterSecret = "test-arbiter-authorization";
    private string connectionString = string.Empty;
    private TestServer? server;
    private int port = 52000;
    public HttpClient Client { get; private set; } = null!;

    private sealed class OnlyReportingControllers : ControllerFeatureProvider
    {
        protected override bool IsController(TypeInfo typeInfo) =>
            typeInfo.AsType() == typeof(GameServerReportsController) || typeInfo.AsType() == typeof(HttpAccessProbeController);
    }

    public Task InitializeAsync()
    {
        connectionString = Environment.GetEnvironmentVariable("GAME_SERVER_TEST_POSTGRES")
            ?? throw new InvalidOperationException("Set GAME_SERVER_TEST_POSTGRES to an isolated *_tests database");
        var settings = new NpgsqlConnectionStringBuilder(connectionString);
        if (settings.Database == null || !settings.Database.EndsWith("_tests", StringComparison.Ordinal))
            throw new InvalidOperationException("Refusing to run reporting tests against a database not ending in _tests");
        var redis = Environment.GetEnvironmentVariable("GAME_SERVER_TEST_REDIS")
            ?? throw new InvalidOperationException("Set GAME_SERVER_TEST_REDIS to an isolated Redis instance");
        Database.Configure(connectionString);
        Services.Cache.Configure(redis);
        Configuration.GameServerAuthorization = Secret;
        Configuration.ArbiterAuthorization = ArbiterSecret;
        Configuration.BaseUrl = "http://reporting.test";
        Configuration.GameServerIp = "127.0.0.1";
        server = new TestServer(new WebHostBuilder().ConfigureServices(services =>
        {
            services.AddLogging();
            services.AddScoped<GameServerService>();
            services.AddControllers().AddApplicationPart(typeof(GameServerReportsController).Assembly)
                .AddApplicationPart(typeof(HttpAccessProbeController).Assembly)
                .ConfigureApplicationPartManager(manager =>
                {
                    manager.FeatureProviders.Clear();
                    manager.FeatureProviders.Add(new OnlyReportingControllers());
                });
        }).Configure(app =>
        {
            app.UseRouting();
            app.UseMiddleware<FrontendProxyMiddleware>();
            app.UseEndpoints(endpoints => endpoints.MapControllers());
        }));
        Client = server.CreateClient();
        return Task.CompletedTask;
    }

    public Task DisposeAsync()
    {
        Client.Dispose();
        server?.Dispose();
        Services.Cache.redLock.Dispose();
        return Task.CompletedTask;
    }

    public async Task<int> Execute(string sql, object? parameters = null)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        return await connection.ExecuteAsync(sql, parameters);
    }

    public async Task<T> Scalar<T>(string sql, object? parameters = null)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        return await connection.QuerySingleAsync<T>(sql, parameters);
    }

    public async Task<long> User()
    {
        var id = await Scalar<long>("INSERT INTO \"user\" (username, password) VALUES (:name, :password) RETURNING id",
            new { name = "report-test-" + Guid.NewGuid().ToString("N"), password = "not-a-real-password-hash" });
        await Execute("INSERT INTO user_economy (user_id, balance_robux, balance_tickets) VALUES (:id, 0, 0)", new { id });
        return id;
    }

    public async Task<long> Place(long creatorId, CreatorType creatorType = CreatorType.User)
    {
        var id = await Scalar<long>(
            "INSERT INTO asset (name, asset_type, asset_genre, creator_type, creator_id, moderation_status) VALUES (:name, 9, 1, :type, :creator, :moderation) RETURNING id",
            new { name = "Reporting test place", type = creatorType, creator = creatorId, moderation = ModerationStatus.ReviewApproved });
        await Execute("INSERT INTO asset_place (asset_id, max_player_count, year) VALUES (:id, 10, 2016)", new { id });
        return id;
    }

    public async Task<Guid> Server(long placeId)
    {
        var id = Guid.NewGuid();
        var serverPort = Interlocked.Increment(ref port);
        await Execute("INSERT INTO asset_server (id, asset_id, ip, port, server_connection) VALUES (:id, :place, '127.0.0.1', :port, :connection)",
            new { id, place = placeId, port = serverPort, connection = "127.0.0.1:" + serverPort });
        return id;
    }
}
