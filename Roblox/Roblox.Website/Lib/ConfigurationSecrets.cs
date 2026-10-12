using Microsoft.Extensions.Configuration;

namespace Roblox.Website.Lib;

public static class ConfigurationSecrets
{
    public static string Require(IConfiguration configuration, string key)
    {
        var value = configuration[key];
        if (string.IsNullOrWhiteSpace(value) || value.Length < 32 || value.Contains("<SECRET>") || value.StartsWith("change-me", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"Configure a private {key} of at least 32 characters. Run scripts/setup.sh to provision missing JWT keys in local-secrets.json.");
        return value;
    }
}
