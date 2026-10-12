-- args: assetUrl, fileExtension, width, height, mannequinUrl
-- Head render: the bundled avatar rig supplies the body, the uploaded mesh
-- replaces its head. No external mannequin asset is downloaded.

local assetUrl, fileExtension, width, height, mannequinUrl = ...

assert(type(assetUrl) == "string" and assetUrl ~= "", "no asset URL was delivered")
assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

local mannequin = game:GetObjects(mannequinUrl)[1]
assert(mannequin ~= nil, "the avatar rig could not be loaded")
if mannequin.Humanoid then
    mannequin.Humanoid.DisplayDistanceType = Enum.HumanoidDisplayDistanceType.None
end
mannequin.Parent = workspace

local head = mannequin:FindFirstChild("Head")
assert(head ~= nil, "the avatar rig has no head")

head.BrickColor = BrickColor.Gray()
local existing = head:FindFirstChild("Mesh")
if existing then
    existing:Destroy()
end

for _, child in pairs(mannequin:GetChildren()) do
    if child:IsA("BasePart") and child.Name ~= "Head" then
        child:Destroy()
    end
end

local mesh = game:GetObjects(assetUrl)[1]
assert(mesh ~= nil, "the head asset could not be loaded")
mesh.Parent = head

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true)
assert(type(image) == "string" and #image > 0, "the head render returned no image data")
return image
