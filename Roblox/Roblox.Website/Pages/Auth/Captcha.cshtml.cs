using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace Roblox.Website.Pages.Auth;

// Keep old bookmarks working without issuing a user-agent bypass cookie.
[IgnoreAntiforgeryToken]
public class Captcha : PageModel
{
    public IActionResult OnGet() => Redirect("/");
    public IActionResult OnPost() => Redirect("/");
}
