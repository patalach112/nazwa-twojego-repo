-- A controlled local ExecuteScript job, not a place bootstrap or game join.
local token, left, right = ...
assert(type(token) == "string", "JSON string argument was not delivered")
assert(left == 19 and right == 23, "JSON number arguments were not delivered")
local part = Instance.new("Part")
part.Parent = game:GetService("Workspace")
assert(part.Parent ~= nil, "The JSON job did not have a working DataModel")
part:Destroy()
return token .. ":" .. tostring(left + right)
