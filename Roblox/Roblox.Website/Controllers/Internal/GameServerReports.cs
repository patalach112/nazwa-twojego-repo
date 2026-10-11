using System.ComponentModel.DataAnnotations;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Mvc;
using Roblox.Services;
using Roblox.Services.Exceptions;

namespace Roblox.Website.Controllers;

[ApiController]
[Route("gs")]
[Consumes("application/json")]
[RequestSizeLimit(65536)]
public class GameServerReportsController : Microsoft.AspNetCore.Mvc.ControllerBase
{
    private readonly GameServerService gameServers;

    public GameServerReportsController(GameServerService gameServers)
    {
        this.gameServers = gameServers;
    }

    private static bool IsAuthorized(string? provided)
    {
        var expected = Configuration.GameServerAuthorization;
        if (string.IsNullOrEmpty(expected) || string.IsNullOrEmpty(provided)) return false;
        return CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(expected), Encoding.UTF8.GetBytes(provided));
    }

    private async Task<IActionResult> ApplyReport(string? authorization, Func<Task> operation)
    {
        if (!IsAuthorized(authorization)) return Unauthorized(new { error = "Unauthorized" });
        try
        {
            await operation();
            return Ok(new { success = true });
        }
        catch (RecordNotFoundException) { return NotFound(new { error = "Unknown game server or player" }); }
        catch (ArgumentException) { return Conflict(new { error = "The place does not match this game server" }); }
        catch (InvalidOperationException) { return Conflict(new { error = "This game server is closing" }); }
    }

    [HttpPost("players/report")]
    public Task<IActionResult> ReportPlayer([FromBody] PlayerReportRequest request)
    {
        return ApplyReport(request.authorization, () => request.eventType == "Join"
            ? gameServers.OnPlayerJoin(request.userId, request.placeId, request.serverId)
            : gameServers.OnPlayerLeave(request.userId, request.placeId, request.serverId));
    }

    [HttpPost("ping")]
    public Task<IActionResult> Ping([FromBody] HeartbeatRequest request)
    {
        return ApplyReport(request.authorization, () => gameServers.SetServerPing(request.serverId, request.placeId,
            request.ping.HasValue ? (long)Math.Round(request.ping.Value) : null,
            request.fps.HasValue ? (long)Math.Round(request.fps.Value) : null));
    }

    [HttpPost("activity")]
    public async Task<IActionResult> Activity([FromBody] ServerRequest request)
    {
        if (!IsAuthorized(request.authorization)) return Unauthorized(new { error = "Unauthorized" });
        return Ok(await gameServers.GetServerActivity(request.serverId));
    }

    // These are notifications, not an RPC back into the reporting process. In
    // particular, legacy game-server calls /gs/delete before it stops its RCC.
    [HttpPost("delete")]
    public Task<IActionResult> Delete([FromBody] ServerRequest request)
    {
        return ApplyReport(request.authorization, () => gameServers.DeleteGameServer(request.serverId));
    }

    [HttpPost("shutdown")]
    public Task<IActionResult> Shutdown([FromBody] PlaceReportRequest request)
    {
        return ApplyReport(request.authorization, () => gameServers.DeleteGameServer(request.serverId, request.placeId));
    }

    public class ServerRequest : IValidatableObject
    {
        public string? authorization { get; set; }
        public Guid serverId { get; set; }

        public IEnumerable<ValidationResult> Validate(ValidationContext context)
        {
            if (serverId == Guid.Empty) yield return new ValidationResult("serverId must be a nonempty UUID", new[] { nameof(serverId) });
        }
    }

    public class PlaceReportRequest : ServerRequest
    {
        [Range(1, long.MaxValue)]
        [JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
        public long placeId { get; set; }
    }

    public class PlayerReportRequest : PlaceReportRequest
    {
        [Range(1, long.MaxValue)]
        [JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
        public long userId { get; set; }
        [Required]
        [RegularExpression("^(Join|Leave)$")]
        public string? eventType { get; set; }
    }

    public class HeartbeatRequest : PlaceReportRequest
    {
        [Range(typeof(decimal), "0", "2147483647")]
        [JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
        public decimal? ping { get; set; }
        [Range(typeof(decimal), "0", "2147483647")]
        [JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
        public decimal? fps { get; set; }
    }
}
