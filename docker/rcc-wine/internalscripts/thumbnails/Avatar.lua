-- args: fileExtension, width, height, mannequinUrl, headColor, torsoColor, leftArmColor, rightArmColor, leftLegColor, rightLegColor, clothingUrls, baseUrl
-- Player thumbnail: the bundled avatar rig is built inside the DataModel, the
-- account's body colours are applied and any local clothing textures are worn.
-- The rig comes from content/avatar so no body-part asset download is required.

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

-- clothingUrls is a semicolon separated list of local asset URLs; every entry is
-- a Shirt, Pants or TeeShirt asset that the rig wears. Anything that fails to
-- load is skipped so a missing optional item cannot break the avatar render.
if type(clothingUrls) == "string" and clothingUrls ~= "" then
    for url in string.gmatch(clothingUrls, "[^;]+") do
        local ok, item = pcall(function() return game:GetObjects(url)[1] end)
        if ok and item ~= nil then
            local previous = rig:FindFirstChild(item.Name)
            if previous and previous.ClassName == item.ClassName then
                previous:Destroy()
            end
            item.Parent = rig
        end
    end
end

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, false, true)
assert(type(image) == "string" and #image > 0, "the avatar render returned no image data")
return image
