using System.Net;
using System.Text;
using Microsoft.AspNetCore.Http.Extensions;
using Microsoft.Net.Http.Headers;
using Roblox.Logging;
using Roblox.Models.Sessions;
using Roblox.Models.Users;
using Roblox.Services;
using Roblox.Website.Lib;
using ServiceProvider = Microsoft.Extensions.DependencyInjection.ServiceProvider;
using System.Text.RegularExpressions;

namespace Roblox.Website.Middleware;

public class FrontendProxyMiddleware
{
    private RequestDelegate _next;

    public FrontendProxyMiddleware(RequestDelegate next)
    {
        _next = next;
        client = sharedClient;
    }

    public static List<string> BypassUrls = new()
    {
        "/apisite/",
        "/swagger/",
        "/api/",
        "/api/economy-chat/",
        // Razor Files
        "/feeds/getuserfeed",
        "/auth/",
        "/IDE/",
        "/membership/notapproved.aspx",
        "/users/filter-friends",
        // Razor Public
        "/unsecuredcontent/",
        // Razor - IDE
        "/IDE/welcome",
        "/ide/publish/editplace",
        "/IDE/Upload.aspx",
        // Razor - Internal
        "/internal/year",
        "/internal/updates",
        "/internal/promocodes",
        "/internal/clothingstealer",
        "/internal/age",
        "/internal/report-abuse",
        "/internal/membership",
        "/internal/apply",
        "/internal/invite",
        "/internal/dev",
        "/internal/faq",
        "/internal/donate",
        "/internal/place-update",
        "/internal/create-place",
        "/internal/migrate-to-application",
        "/internal/collectibles",
        "/internal/contest/first-contest",
        "/internal/tixexchange",
        "/internal/robuxexchange",
        "/internal/referral",
        "/auth/notapproved",
        // Admin
        "/admin-api/api",
        "/admin",
        // Web
        "/thumbs/avatar.ashx",
        "/asset-gameicon/multiget",
        "/thumbs/avatar-headshot.ashx",
        "/thumbs/asset.ashx",
        "/Thumbs/GameIcon.ashx",
        "/user-sponsorship/",
        "/users/inventory/list-json",
        "/users/favorites/list-json",
        "/userads/redirect",
        "/users/profile/robloxcollections-json",
        "/asset/toggle-profile",
        "/comments/get-json",
        "/comments/post",
        "/usercheck/show-tos",
        "/search/users/results",
        "/users/set-builders-club",
        // Web - Game
        "/game/get-join-script",
        "/game/placelauncher.ashx",
        "/placelauncher.ashx",
        "/game/join.ashx",
        "/game/validate-machine",
        "/game/validateticket.ashx",
        "/game/get-join-script-debug",
        "/games/getgameinstancesjson",
        "/develop/upload",
        // rbxapi
        "/v1",
        // gs
        "/gs/activity",
        "/gs/ping",
        "/gs/delete",
        "/gs/shutdown",
        "/gs/players/report",
        "/gs/a",
        "/api/moderation/filtertext",
        "/moderation/filtertext",
        "/moderation/v2/filtertext",
        // hubs
        "/chat",
        "/chat/negotiate",
    };

    private static readonly HttpClient sharedClient = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromSeconds(30),
    };
    private readonly HttpClient client;
    private static readonly HashSet<string> hopHeaders = new(StringComparer.OrdinalIgnoreCase)
    {
        "Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization", "TE", "Trailer",
        "Transfer-Encoding", "Upgrade", "Content-Length", "Host",
    };

    public FrontendProxyMiddleware(RequestDelegate next, HttpClient client) : this(next)
    {
        this.client = client;
    }

    public async Task InvokeAsync(HttpContext ctx)
    {
        var path = ctx.Request.Path.ToString();
        // /api/proxy is a Next API route, unlike the backend's other /api routes.
        var nextApiProxy = string.Equals(path, "/api/proxy", StringComparison.OrdinalIgnoreCase);
        if (!nextApiProxy && (path.Contains("/canmanage/") || path.Contains("filter-friends")
            || path.Contains("multiget-friend-requests") || path.Contains("AbuseReport")
            || Regex.IsMatch(path, @"^/places/\d+/settings$", RegexOptions.IgnoreCase)
            || Regex.IsMatch(path, @"^/users/\d+$", RegexOptions.IgnoreCase)
            || path.Contains("/universes/", StringComparison.OrdinalIgnoreCase)
            || BypassUrls.Any(prefix => path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))))
        {
            await _next(ctx);
            return;
        }

        var origin = FrontendProxyTarget.ParseBaseUrl(Roblox.Configuration.FrontendBaseUrl);
        var target = FrontendProxyTarget.Resolve(origin, ctx.Request.GetEncodedPathAndQuery());
        using var request = new HttpRequestMessage(new HttpMethod(ctx.Request.Method), target);
        if (ctx.Request.ContentLength > 0 || ctx.Request.Headers.ContainsKey("Transfer-Encoding"))
            request.Content = new StreamContent(ctx.Request.Body);
        foreach (var header in ctx.Request.Headers)
        {
            if (hopHeaders.Contains(header.Key)) continue;
            if (!request.Headers.TryAddWithoutValidation(header.Key, header.Value.ToArray()))
                request.Content?.Headers.TryAddWithoutValidation(header.Key, header.Value.ToArray());
        }
        request.Headers.Remove("X-Forwarded-Host");
        request.Headers.TryAddWithoutValidation("X-Forwarded-Host", ctx.Request.Host.ToString());

        try
        {
            using var result = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ctx.RequestAborted);
            ctx.Response.StatusCode = (int)result.StatusCode;
            foreach (var header in result.Headers.Concat(result.Content.Headers))
            {
                if (!hopHeaders.Contains(header.Key)) ctx.Response.Headers[header.Key] = header.Value.ToArray();
            }
            // Never share cached personalized HTML between browser sessions.
            if (result.StatusCode == HttpStatusCode.NotFound || (int)result.StatusCode >= 500)
                ctx.Response.Headers.CacheControl = "no-store";
            if (!HttpMethods.IsHead(ctx.Request.Method))
                await result.Content.CopyToAsync(ctx.Response.Body, ctx.RequestAborted);
        }
        catch (Exception error) when (error is HttpRequestException || error is TaskCanceledException && !ctx.RequestAborted.IsCancellationRequested)
        {
            ctx.Response.StatusCode = StatusCodes.Status502BadGateway;
            await ctx.Response.WriteAsync("The frontend service is unavailable.", ctx.RequestAborted);
        }
    }
}
