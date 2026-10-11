using Roblox.Libraries.TwitterApi;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class OptionalIntegrationTests
{
    [Fact]
    public async Task MissingTwitterCredentialsDisableOnlyTwitterAndDoNotSendUnauthenticatedRequests()
    {
        TwitterApi.Configure(null);
        TwitterApi.Configure("");
        var error = await Assert.ThrowsAsync<InvalidOperationException>(() => new TwitterApi().GetUserByScreenName("local-test"));
        Assert.Contains("Twitter:Bearer", error.Message);
        Assert.Contains("disabled", error.Message);
    }
}
