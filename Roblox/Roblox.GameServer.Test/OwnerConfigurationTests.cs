using Microsoft.Extensions.Configuration;
using Roblox.Website.Filters;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class OwnerConfigurationTests : IDisposable
{
    private readonly ReportingFixture fixture;
    public OwnerConfigurationTests(ReportingFixture fixture) { this.fixture = fixture; }
    public void Dispose() => StaffFilter.Configure(new List<long>());

    [Fact]
    public async Task AnEmptyOwnerArrayDoesNotBreakAnOrdinaryAccountSession()
    {
        using var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes("{\"OwnerUserId\":[]}"));
        var configuration = new ConfigurationBuilder().AddJsonStream(stream).Build();
        StaffFilter.Configure(configuration.GetSection("OwnerUserId").Get<List<long>>());
        Assert.False(StaffFilter.IsOwner(1));
        Assert.False(StaffFilter.IsOwner(12));
        Assert.False(await StaffFilter.IsStaff(await fixture.User()));
    }

    [Fact]
    public void OnlyExplicitIdsReceiveOwnerAccessAndCallerMutationDoesNotGrantMore()
    {
        var ids = new List<long> {42};
        StaffFilter.Configure(ids);
        ids.Add(43);
        Assert.True(StaffFilter.IsOwner(42));
        Assert.False(StaffFilter.IsOwner(43));
    }
}
