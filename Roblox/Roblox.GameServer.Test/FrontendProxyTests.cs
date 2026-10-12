using System.Net;
using System.Text;
using Microsoft.AspNetCore.Http;
using Roblox.Website.Lib;
using Roblox.Website.Middleware;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class FrontendProxyTests : IDisposable
{
    private readonly string originalAddress = Configuration.FrontendBaseUrl;
    public FrontendProxyTests(ReportingFixture fixture) { Configuration.FrontendBaseUrl = "http://frontend:3000"; }
    public void Dispose() => Configuration.FrontendBaseUrl = originalAddress;

    private sealed class ProbeHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> callback) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => callback(request);
    }

    [Fact]
    public async Task TheNextApiProxyForwardsMethodBodyCookieAndBinaryResponse()
    {
        var payload = Encoding.UTF8.GetBytes("{\"value\":1}");
        var responseBytes = new byte[] {0, 255, 128, 42};
        using var client = new HttpClient(new ProbeHandler(async request =>
        {
            Assert.Equal("http://frontend:3000/api/proxy?url=test", request.RequestUri!.AbsoluteUri);
            Assert.Equal(HttpMethod.Post, request.Method);
            Assert.Equal(payload, await request.Content!.ReadAsByteArrayAsync());
            Assert.Equal("unit-test-cookie", request.Headers.GetValues("Cookie").Single());
            var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(responseBytes) };
            response.Headers.TryAddWithoutValidation("Set-Cookie", "unit-cookie=1; HttpOnly; Path=/");
            return response;
        }));
        var context = new DefaultHttpContext();
        context.Request.Method = "POST";
        context.Request.Path = "/api/proxy";
        context.Request.QueryString = new QueryString("?url=test");
        context.Request.Host = new HostString("localhost", 5000);
        context.Request.Headers.Cookie = "unit-test-cookie";
        context.Request.ContentType = "application/json";
        context.Request.ContentLength = payload.Length;
        context.Request.Body = new MemoryStream(payload);
        context.Response.Body = new MemoryStream();
        await new FrontendProxyMiddleware(_ => throw new Exception("Next API requests must be proxied"), client).InvokeAsync(context);
        Assert.Equal(200, context.Response.StatusCode);
        Assert.Equal(responseBytes, ((MemoryStream)context.Response.Body).ToArray());
        Assert.Contains("HttpOnly", context.Response.Headers.SetCookie.ToString());
    }

    [Fact]
    public async Task PersonalizedResponsesAreNotSharedBetweenRequests()
    {
        var calls = 0;
        using var client = new HttpClient(new ProbeHandler(request => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("response-" + ++calls),
        })));
        var middleware = new FrontendProxyMiddleware(_ => Task.CompletedTask, client);
        foreach (var expected in new[] {"response-1", "response-2"})
        {
            var context = new DefaultHttpContext();
            context.Request.Method = "GET";
            context.Request.Path = "/home";
            context.Response.Body = new MemoryStream();
            await middleware.InvokeAsync(context);
            Assert.Equal(expected, Encoding.UTF8.GetString(((MemoryStream)context.Response.Body).ToArray()));
        }
    }

    [Fact]
    public async Task BackendRoutesStillBypassTheFrontend()
    {
        var passed = false;
        using var client = new HttpClient(new ProbeHandler(_ => throw new Exception("Backend routes must not be proxied")));
        var context = new DefaultHttpContext();
        context.Request.Path = "/apisite/users/v1/users/authenticated";
        await new FrontendProxyMiddleware(_ => {passed = true; return Task.CompletedTask;}, client).InvokeAsync(context);
        Assert.True(passed);
    }

    [Theory]
    [InlineData("http://frontend:3000/path")]
    [InlineData("http://user:password@frontend:3000/")]
    [InlineData("file:///tmp/frontend")]
    public void FrontendConfigurationCannotEscapeAnHttpOrigin(string address)
    {
        Assert.Throws<ArgumentException>(() => FrontendProxyTarget.ParseBaseUrl(address));
    }
}
