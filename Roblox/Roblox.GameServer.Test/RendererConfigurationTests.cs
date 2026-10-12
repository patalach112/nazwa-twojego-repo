using Roblox.Rendering;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class RendererConfigurationTests : IDisposable
{
    public void Dispose() => RenderingHandler.Configure();

    [Fact]
    public void ALocalSetupDoesNotContactTheOriginalThirdPartyRenderer()
    {
        RenderingHandler.Configure();
        Assert.False(RenderingHandler.IsConfigured);
    }

    [Fact]
    public async Task DisabledRenderingFailsImmediatelyWithConfigurationAdvice()
    {
        RenderingHandler.Configure();
        var error = await Assert.ThrowsAsync<InvalidOperationException>(() => RenderingHandler.RequestHatThumbnail(1));
        Assert.Contains("Render:BaseUrl", error.Message);
    }

    [Fact]
    public void AnOwnedDockerServiceIsAValidRendererAddress()
    {
        RenderingHandler.Configure("http://renderer:6424", "test-renderer-secret");
        Assert.True(RenderingHandler.IsConfigured);
    }

    [Theory]
    [InlineData("file:///tmp/renderer")]
    [InlineData("http://user:password@renderer/")]
    [InlineData("http://renderer/?token=secret")]
    [InlineData("http://renderer/#fragment")]
    [InlineData("not-a-url")]
    public void InvalidRendererAddressesAreRejected(string address)
    {
        Assert.Throws<ArgumentException>(() => RenderingHandler.Configure(address));
    }
}
