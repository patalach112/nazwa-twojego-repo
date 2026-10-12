-- args: fileExtension, width, height, mannequinUrl, headColor, torsoColor, leftArmColor, rightArmColor, leftLegColor, rightLegColor, clothingUrls, baseUrl
-- Headshot: the same rig as Avatar.lua, framed on the head through the camera
-- focus call the legacy closeup script used. No external mannequin asset needed.

local fileExtension, width, height, mannequinUrl = ...
local headColor, torsoColor, leftArmColor, rightArmColor, leftLegColor, rightLegColor = ...
local clothingUrls, baseUrl = ...

assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")
assert(type(mannequinUrl) == "string" and mannequinUrl ~= "", "no avatar rig URL was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true
if type(baseUrl) == "string" and baseUrl ~= "" then
    pcall(function() game:GetService("ContentProvider"):SetBaseUrl(baseUrl) end)
end

local rig = game:GetObjects(mannequinUrl)[1]
assert(rig ~= nil, "the avatar rig could not be loaded")
if rig.Humanoid then
    rig.Humanoid.DisplayDistanceType = Enum.HumanoidDisplayDistanceType.None
end
rig.Parent = workspace

local colours = {
    Head = headColor,
    Torso = torsoColor,
    ["Left Arm"] = leftArmColor,
    ["Right Arm"] = rightArmColor,
    ["Left Leg"] = leftLegColor,
    ["Right Leg"] = rightLegColor,
}
for name, number in pairs(colours) do
    local part = rig:FindFirstChild(name)
    if part and type(number) == "number" and number > 0 then
        part.BrickColor = BrickColor.new(number)
    end
end

local head = rig:FindFirstChild("Head")
assert(head ~= nil, "the avatar rig has no head")
if head:FindFirstChildOfClass("SpecialMesh") == nil and head:FindFirstChild("Mesh") == nil then
    -- The bundled rigs already carry their head mesh; only destroy the body so
    -- the framing stays on the head if a rig ships without one.
    for _, child in pairs(rig:GetChildren()) do
        if child:IsA("BasePart") and child.Name ~= "Head" then
            child:Destroy()
        end
    end
end

local camera = workspace.CurrentCamera
if camera then
    camera.CameraType = Enum.CameraType.Scriptable
    local focus = head.Position + Vector3.new(0, 0.5, 0)
    pcall(function()
        camera.CFrame = CFrame.new(focus + Vector3.new(0, 0.2, 3.2), focus)
    end)
end

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true, true)
assert(type(image) == "string" and #image > 0, "the headshot render returned no image data")
return image
