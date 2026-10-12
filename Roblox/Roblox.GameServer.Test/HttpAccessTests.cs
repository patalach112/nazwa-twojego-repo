using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc;
using Roblox.Models.Staff;
using Roblox.Services.Exceptions;
using Roblox.Website.Filters;
using Xunit;

namespace Roblox.GameServer.Test;

// Test-only endpoints use the same session accessor and staff filter as the site.
[ApiController]
[Route("api/http-access-probe")]
public class HttpAccessProbeController : Roblox.Website.Controllers.ControllerBase
{
    [HttpGet("public")]
    public IActionResult PublicGet() => Ok(new { reached = true });

    [HttpPost("public")]
    public IActionResult PublicPost() => Ok(new { reached = true });

    [HttpGet("account")]
    public IActionResult Account()
    {
        try { return Ok(new { userId = safeUserSession.userId }); }
        catch (RobloxException error) { return StatusCode(error.statusCode, new { error = "Unauthorized" }); }
    }

    [HttpGet("staff")]
    [StaffFilter(Access.GetStats)]
    public IActionResult Staff() => Ok(new { reached = true });
}

[Collection("Reporting")]
public class HttpAccessTests
{
    private readonly ReportingFixture fixture;
    public HttpAccessTests(ReportingFixture fixture) { this.fixture = fixture; }

    [Theory]
    [InlineData(null)]
    [InlineData("curl/8.0")]
    [InlineData("axios/1.9.0")]
    [InlineData("Mozilla/5.0 Chrome/60.0")]
    public async Task PublicRequestsDoNotNeedAGuardCookieOrGlobalCsrfToken(string? userAgent)
    {
        foreach (var method in new[] { HttpMethod.Get, HttpMethod.Post })
        {
            using var request = new HttpRequestMessage(method, "/api/http-access-probe/public");
            if (method == HttpMethod.Post) request.Content = JsonContent.Create(new { value = "test" });
            if (userAgent != null) request.Headers.Add("User-Agent", userAgent);
            // Stale cookies from an older checkout must not reintroduce the gate.
            request.Headers.Add("Cookie", "rbxcsrf4=old-invalid-cookie; uabypass1=old-invalid-cookie");
            using var response = await fixture.Client.SendAsync(request);
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            Assert.Null(response.Headers.Location);
            Assert.False(response.Headers.Contains("x-csrf-token"));
            Assert.False(response.Headers.Contains("Set-Cookie"));
        }
    }

    [Theory]
    [InlineData(null)]
    [InlineData("curl/8.0")]
    [InlineData("Roblox/WinInet")]
    public async Task RemovingBrowserGatesDoesNotAuthorizeAccountsOrStaff(string? userAgent)
    {
        foreach (var (route, expected) in new[] { ("account", HttpStatusCode.Unauthorized), ("staff", HttpStatusCode.Forbidden) })
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, "/api/http-access-probe/" + route);
            if (userAgent != null) request.Headers.Add("User-Agent", userAgent);
            request.Headers.Add("rblx-authorization", "no-longer-an-access-key");
            using var response = await fixture.Client.SendAsync(request);
            Assert.Equal(expected, response.StatusCode);
        }
    }

    [Fact]
    public void TheOldCaptchaRouteRedirectsWithoutValidatingACaptchaOrIssuingABypassCookie()
    {
        var page = new Roblox.Website.Pages.Auth.Captcha();
        Assert.Equal("/", Assert.IsType<RedirectResult>(page.OnGet()).Url);
        Assert.Equal("/", Assert.IsType<RedirectResult>(page.OnPost()).Url);
    }
}
