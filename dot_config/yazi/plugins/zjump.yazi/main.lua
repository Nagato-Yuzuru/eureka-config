--- Explicit zoxide jumps that record their target, mirroring the shell's `z` / `zi`.
--- Plain navigation inside Yazi is never recorded.
---   plugin zjump -- query        prompt for `z` arguments
---   plugin zjump -- interactive  pick from zoxide's list, like `zi`

local get_cwd = ya.sync(function() return tostring(cx.active.current.cwd) end)

local function fail(content) ya.notify { title = "zjump", content = content, timeout = 5, level = "error" } end

local function jump(target)
	ya.emit("cd", { target, raw = true })
	Command("zoxide"):arg({ "add", "--", target }):status()
end

-- Resolve a path argument the way the shell's `cd` would: relative to cwd, `~` expanded,
-- `..` normalized. Returns nil when it is not an existing directory.
local function resolve_dir(cwd, arg)
	local output = Command("sh")
		:arg({ "-c", 'case $1 in "~"|"~/"*) set -- "$HOME${1#\\~}";; esac; cd -- "$1" 2>/dev/null && pwd', "sh", arg })
		:cwd(cwd)
		:output()
	if output and output.status.success then
		return (output.stdout:gsub("\n$", ""))
	end
end

local function query(cwd)
	local value, event = ya.input {
		pos = { "top-center", y = 2, w = 50 },
		title = "Zoxide:",
	}
	if event ~= 1 then
		return
	end

	local args = {}
	for word in value:gmatch("%S+") do
		args[#args + 1] = word
	end

	if #args == 0 then
		return jump(os.getenv("HOME"))
	elseif #args == 1 then
		local dir = resolve_dir(cwd, args[1])
		if dir then
			return jump(dir)
		end
	end

	local output, err = Command("zoxide"):arg({ "query", "--exclude", cwd, "--" }):arg(args):output()
	if not output then
		return fail(string.format("Failed to run `zoxide`: %s", err))
	elseif not output.status.success then
		return fail((output.stderr:gsub("^zoxide:%s*", ""):gsub("\n$", "")))
	end
	jump((output.stdout:gsub("\n$", "")))
end

-- Reuses the built-in zoxide plugin's fzf picker so the look and options stay identical.
local function interactive(cwd)
	local zoxide = require("zoxide")
	local permit = ui.hide()
	local target, err = zoxide.run_with(cwd)
	permit:drop()
	if not target then
		fail(tostring(err))
	elseif target ~= "" then
		jump(target)
	end
end

return {
	entry = function(_, job)
		local cwd = get_cwd()
		if job.args[1] == "interactive" then
			interactive(cwd)
		else
			query(cwd)
		end
	end,
}
