using Roblox.Libraries.Captcha;
using Roblox.Services;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class CaptchaConfigurationTests : IDisposable
{
    private readonly ReportingFixture fixture;
    private readonly bool wasEnabled = HCaptcha.IsEnabled;
    private readonly string? publicKey = Configuration.HCaptchaPublicKey;
    private readonly string? privateKey = Configuration.HCaptchaPrivateKey;

    public CaptchaConfigurationTests(ReportingFixture fixture) { this.fixture = fixture; }
    public void Dispose() => HCaptcha.Configure(wasEnabled, publicKey, privateKey);

    [Fact]
    public async Task EmptyLocalConfigDoesNotRequireACaptchaResponseOrNetworkService()
    {
        HCaptcha.Configure(null, "", "");
        Assert.False(HCaptcha.IsEnabled);
        Assert.True(await HCaptcha.IsValid("127.0.0.1", null));
    }

    [Fact]
    public async Task ExplicitDisableOverridesExistingProviderKeys()
    {
        HCaptcha.Configure(false, "test-public", "test-private");
        Assert.False(HCaptcha.IsEnabled);
        Assert.True(await HCaptcha.IsValid("127.0.0.1", ""));
    }

    [Fact]
    public async Task LegacyConfiguredDeploymentsStillRequireAResponse()
    {
        HCaptcha.Configure(null, "test-public", "test-private");
        Assert.True(HCaptcha.IsEnabled);
        Assert.False(await HCaptcha.IsValid("127.0.0.1", null));
    }

    [Fact]
    public void ExplicitEnableWithoutKeysFailsWithConfigurationAdvice()
    {
        var error = Assert.Throws<InvalidOperationException>(() => HCaptcha.Configure(true, "", ""));
        Assert.Contains("HCaptcha:Enabled", error.Message);
    }

    [Fact]
    public async Task DisablingCaptchaDoesNotMakeAnIncorrectPasswordValid()
    {
        HCaptcha.Configure(false, "", "");
        var user = await fixture.User();
        using var users = new UsersService();
        await users.UpdatePassword(user, "correct-local-test-password");
        Assert.True(await users.VerifyPassword(user, "correct-local-test-password"));
        Assert.False(await users.VerifyPassword(user, "incorrect-local-test-password"));
    }
}
