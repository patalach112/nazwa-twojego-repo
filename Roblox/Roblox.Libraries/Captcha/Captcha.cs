using System.Net;
using System.Text.Json;
using Roblox.Logging;

namespace Roblox.Libraries.Captcha;

internal class HCaptchaJsonResponse
{
    public bool success { get; set; }
}

public class HCaptcha
{
    private static HttpClient client { get; } = new();
    public static bool IsEnabled { get; private set; }

    public static void Configure(bool? enabled, string? publicKey, string? privateKey)
    {
        var keysConfigured = !string.IsNullOrWhiteSpace(publicKey) && !string.IsNullOrWhiteSpace(privateKey);
        // Preserve older configured deployments, but empty local configs need no
        // Cloudflare account. An explicit Enabled setting always takes precedence.
        IsEnabled = enabled ?? keysConfigured;
        Roblox.Configuration.HCaptchaPublicKey = publicKey ?? string.Empty;
        Roblox.Configuration.HCaptchaPrivateKey = privateKey ?? string.Empty;
        if (IsEnabled && !keysConfigured)
            throw new InvalidOperationException("Enabled captcha requires HCaptcha:Public and HCaptcha:Private, or set HCaptcha:Enabled to false for local development");
    }

    public static async Task<bool> IsValid(string rawIpAddress, string? captchaResponse)
    {
        if (!IsEnabled) return true;
        if (string.IsNullOrWhiteSpace(captchaResponse) || string.IsNullOrWhiteSpace(Roblox.Configuration.HCaptchaPrivateKey) || string.IsNullOrWhiteSpace(Roblox.Configuration.HCaptchaPublicKey))
            return false;
        try
        {
            using var cont = new FormUrlEncodedContent(new Dictionary<string,string>
            {
                {"response", captchaResponse},
                {"secret", Roblox.Configuration.HCaptchaPrivateKey},
                // {"remoteip", rawIpAddress}, // Adding the IP causes all captcha to fail. It's also probably not great for privacy either. Makes it easier to bot though, since you wouldn't need a proxy you can share with your captcha solvers...
                {"sitekey", Roblox.Configuration.HCaptchaPublicKey},
            });

            using var result = await client.PostAsync("https://challenges.cloudflare.com/turnstile/v0/siteverify", cont);
            var str = await result.Content.ReadAsStringAsync();
            if (result.StatusCode != HttpStatusCode.OK)
            {
                Writer.Info(LogGroup.Captcha, "Unsuccessful captcha - Bad Status. Status = {0} Full body = {1}", result.StatusCode, str);
                return false; // todo: should probably log this
            }
            
            var decoded = JsonSerializer.Deserialize<HCaptchaJsonResponse>(str);
            if (decoded is not {success: true})
            {
                Writer.Info(LogGroup.Captcha, "Unsuccessful captcha. Full body = {0}", str);
                return false;
            }

            return true;
        }
        catch (Exception e)
        {
            Writer.Info(LogGroup.Captcha, "Captcha failure {0}", e.Message);
            // todo: should probably log
            return false;
        }
    }
}
