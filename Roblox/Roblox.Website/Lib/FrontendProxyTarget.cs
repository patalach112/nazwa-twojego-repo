namespace Roblox.Website.Lib;

public static class FrontendProxyTarget
{
    public static Uri ParseBaseUrl(string address)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var url)
            || (url.Scheme != Uri.UriSchemeHttp && url.Scheme != Uri.UriSchemeHttps)
            || !string.IsNullOrEmpty(url.UserInfo) || url.AbsolutePath != "/"
            || !string.IsNullOrEmpty(url.Query) || !string.IsNullOrEmpty(url.Fragment))
            throw new ArgumentException("FrontendBaseUrl must be an HTTP(S) origin without credentials or a path");
        return url;
    }

    public static Uri Resolve(Uri origin, string pathAndQuery)
    {
        if (!pathAndQuery.StartsWith('/')) throw new ArgumentException("Expected a local frontend request path");
        var target = new Uri(origin.AbsoluteUri.TrimEnd('/') + pathAndQuery);
        if (target.Scheme != origin.Scheme || target.Host != origin.Host || target.Port != origin.Port)
            throw new ArgumentException("Frontend request escaped its configured origin");
        return target;
    }
}
