namespace Roblox.Website.Lib;

public static class SessionCookieOptions
{
    public static CookieOptions Create(string? publicBaseUrl)
    {
        // HTTPS deployments always keep Secure, including TLS terminated at a
        // reverse proxy. Explicit HTTP local setups must be able to send cookies.
        var secure = !Uri.TryCreate(publicBaseUrl, UriKind.Absolute, out var uri)
            || uri.Scheme != Uri.UriSchemeHttp;
        return new CookieOptions
        {
            Secure = secure,
            HttpOnly = true,
            Expires = DateTimeOffset.UtcNow.AddDays(364),
            IsEssential = true,
            Path = "/",
            SameSite = SameSiteMode.Lax,
        };
    }
}
