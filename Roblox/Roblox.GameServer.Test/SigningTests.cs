using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Roblox.Services.Signer;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public sealed class SigningTests : IDisposable
{
    private readonly string root = Path.Combine(Path.GetTempPath(), "pekora-signing-test-" + Guid.NewGuid());
    private readonly RSACryptoServiceProvider legacyKey = new(1024);
    private readonly RSA modernKey = RSA.Create(2048);

    public SigningTests()
    {
        // Never inspect or depend on the original private keys shipped in the repo.
        var keys = Path.Combine(root, "Keys");
        Directory.CreateDirectory(keys);
        File.WriteAllText(Path.Combine(keys, "PrivateKeyBlob.txt"), Convert.ToBase64String(legacyKey.ExportCspBlob(true)));
        File.WriteAllText(Path.Combine(keys, "PrivateKey2048.pem"), modernKey.ExportPkcs8PrivateKeyPem());
        SignService.Setup(root);
    }

    public void Dispose()
    {
        legacyKey.Dispose();
        modernKey.Dispose();
        Directory.Delete(root, true);
    }

    private static void VerifyEnvelope(string response, string tag, RSA key)
    {
        var parts = response.Split('%', 3);
        Assert.Equal(tag, parts[0]);
        Assert.StartsWith("\r\n", parts[2]);
        Assert.True(key.VerifyData(Encoding.UTF8.GetBytes(parts[2]), Convert.FromBase64String(parts[1]), HashAlgorithmName.SHA1, RSASignaturePadding.Pkcs1));
    }

    [Fact]
    public void PortableKeySetupProducesVerifiableLegacyEnvelopes()
    {
        using var signer = new SignService();
        VerifyEnvelope(signer.SignJsonResponseForClientFromPrivateKey(new { text = "Gdańsk", id = 123 }), "--rbxsig", legacyKey);
        VerifyEnvelope(signer.SignStringResponseForClientFromPrivateKey("return 'Gdańsk'", true), "--rbxsig", legacyKey);
        var raw = signer.SignStringResponseForClientFromPrivateKey("123\njob\ntimestamp");
        Assert.True(legacyKey.VerifyData(Encoding.UTF8.GetBytes("123\njob\ntimestamp"), Convert.FromBase64String(raw), HashAlgorithmName.SHA1, RSASignaturePadding.Pkcs1));
    }

    [Fact]
    public void ModernEnvelopesUseTheImported2048BitKeyNotAnUnrelatedRandomKey()
    {
        using var signer = new SignService();
        VerifyEnvelope(signer.SignJson2048(new { id = 123 }), "--rbxsig2", modernKey);
        VerifyEnvelope(signer.SignString2048("return 123", true), "--rbxsig2", modernKey);
        var raw = signer.SignString2048("123\njob\ntimestamp");
        Assert.True(modernKey.VerifyData(Encoding.UTF8.GetBytes("123\njob\ntimestamp"), Convert.FromBase64String(raw), HashAlgorithmName.SHA1, RSASignaturePadding.Pkcs1));
    }

    [Fact]
    public async Task ConcurrentSignaturesDoNotShareAMutableHashAlgorithm()
    {
        var responses = await Task.WhenAll(Enumerable.Range(0, 12).Select(i => Task.Run(() =>
        {
            using var signer = new SignService();
            return signer.SignStringResponseForClientFromPrivateKey("return " + i, true);
        })));
        foreach (var response in responses) VerifyEnvelope(response, "--rbxsig", legacyKey);
    }

    [Fact]
    public void ClientTicketDatesUseTheProtocolCultureEvenOnAPolishHost()
    {
        var culture = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("pl-PL");
            using var signer = new SignService();
            var ticket = signer.GenerateClientTicketV2(123, "Player", Guid.NewGuid(), "http://example.test/avatar");
            Assert.Matches(@"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} (AM|PM);", ticket);
        }
        finally { CultureInfo.CurrentCulture = culture; }
    }
}
