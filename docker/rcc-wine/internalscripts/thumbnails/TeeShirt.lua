-- args: assetUrl, fileExtension, width, height, mannequinUrl
-- T-shirt render: same framing as Clothing.lua, on the bundled avatar rig.

local assetUrl, fileExtension, width, height, mannequinUrl = ...

assert(type(assetUrl) == "string" and assetUrl ~= "", "no asset URL was delivered")
assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

local rig = game:GetObjects(mannequinUrl)[1]
assert(rig ~= nil, "the avatar rig could not be loaded")
if rig.Humanoid then
    rig.Humanoid.DisplayDistanceType = Enum.HumanoidDisplayDistanceType.None
end
rig.Parent = workspace

local shirt = game:GetObjects(assetUrl)[1]
assert(shirt ~= nil, "the t-shirt asset could not be loaded")
local previous = rig:FindFirstChild(shirt.Name)
if previous and previous.ClassName == shirt.ClassName then
    previous:Destroy()
end
shirt.Parent = rig

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true, true)
assert(type(image) == "string" and #image > 0, "the t-shirt render returned no image data")
return image
