using Roblox.Models.Economy;
using Roblox.Services;
using Xunit;

namespace Roblox.GameServer.Test;

[Collection("Reporting")]
public class EarningsTests
{
    private readonly ReportingFixture db;
    public EarningsTests(ReportingFixture db) { this.db = db; }

    [Fact]
    public async Task NullSubtypeMeansAllEarningsOfThatTypeWithinTheRequestedPeriod()
    {
        var user = await db.User();
        await db.Execute("INSERT INTO user_transaction (user_id_one, user_id_two, type, currency_type, amount, sub_type, created_at) VALUES (:id, 1, 100, 2, 10, NULL, now()), (:id, 1, 100, 2, 20, :subtype, now()), (:id, 1, 100, 2, 200, :subtype, :old), (:id, 1, 101, 2, 300, NULL, now())",
            new { id = user, subtype = TransactionSubType.ItemPurchase, old = DateTime.UtcNow.Subtract(TimeSpan.FromHours(13)) });
        using var economy = new EconomyService();
        Assert.Equal(30L, await economy.CountTransactionEarningsOfType(user, PurchaseType.PlayingGame, null, TimeSpan.FromHours(12)));
        Assert.Equal(20L, await economy.CountTransactionEarningsOfType(user, PurchaseType.PlayingGame, TransactionSubType.ItemPurchase, TimeSpan.FromHours(12)));
        Assert.Equal(230L, await economy.CountTransactionEarningsOfType(user, PurchaseType.PlayingGame, null, TimeSpan.FromHours(24)));
    }

    [Fact]
    public async Task AnEmptyEarningsPeriodReturnsZero()
    {
        var user = await db.User();
        using var economy = new EconomyService();
        Assert.Equal(0L, await economy.CountTransactionEarningsOfType(user, PurchaseType.PlayingGame, null, TimeSpan.FromHours(12)));
    }
}
