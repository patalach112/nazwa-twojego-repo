-- args: assetUrl, fileExtension, width, height, mannequinUrl
-- Shirt and pants render: the bundled avatar rig wears the uploaded clothing
-- texture, which is the catalog picture for clothing items.

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

local clothing = game:GetObjects(assetUrl)[1]
assert(clothing ~= nil, "the clothing asset could not be loaded")
local previous = rig:FindFirstChild(clothing.Name)
if previous and previous.ClassName == clothing.ClassName then
    previous:Destroy()
end
clothing.Parent = rig

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true, true)
assert(type(image) == "string" and #image > 0, "the clothing render returned no image data")
return image
