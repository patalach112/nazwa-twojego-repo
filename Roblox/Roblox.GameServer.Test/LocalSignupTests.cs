using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Roblox.Services.App.FeatureFlags;
using Roblox.Website.Lib;
using Roblox.Website.Pages.Auth;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class LocalSignupTests : IDisposable
{
    private readonly bool originalLocalRegistration = Configuration.LocalRegistrationEnabled;
    public LocalSignupTests(ReportingFixture fixture) { FeatureFlags.UpdateFlags(); }
    public void Dispose() => Configuration.LocalRegistrationEnabled = originalLocalRegistration;

    [Fact]
    public async Task LocalSignupRendersWithoutAnExternalApplication()
    {
        Configuration.LocalRegistrationEnabled = true;
        var signup = new Signup { PageContext = new PageContext { HttpContext = new DefaultHttpContext() } };
        Assert.IsType<PageResult>(await signup.OnGet());
        Assert.True(signup.localRegistration);
    }

    [Fact]
    public async Task AnExistingApplicationOnlyDeploymentStillRequiresApproval()
    {
        Configuration.LocalRegistrationEnabled = false;
        var signup = new Signup { PageContext = new PageContext { HttpContext = new DefaultHttpContext() } };
        var redirect = Assert.IsType<RedirectResult>(await signup.OnGet());
        Assert.Equal("/auth/application", redirect.Url);
        Assert.False(signup.localRegistration);
    }

    [Theory]
    [InlineData("http://localhost:3000", false)]
    [InlineData("http://192.168.1.10:3000", false)]
    [InlineData("https://example.test", true)]
    [InlineData("invalid", true)]
    [InlineData(null, true)]
    public void LocalHttpCookiesWorkWithoutWeakeningHttpsCookies(string? address, bool secure)
    {
        var options = SessionCookieOptions.Create(address);
        Assert.Equal(secure, options.Secure);
        Assert.True(options.HttpOnly);
        Assert.Equal("/", options.Path);
        Assert.Equal(SameSiteMode.Lax, options.SameSite);
    }
}
