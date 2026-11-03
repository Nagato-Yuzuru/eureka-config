-- Close pane / tab / session behind a y/n confirmation, like tmux's
-- confirm-before on x and &. Each close key enters a one-shot exclusive mode:
-- y closes, n or Esc cancels, and nothing else reaches the terminal.
--
-- The closes call the server directly: client pane.close bound to a key does
-- nothing on app 0.1.0 (1408), see Rex feedback "pane.close key binding
-- stopped working".

local prefix = require("keys.prefix")
local panes = require("keys.panes")
local check = panes.check

local closers = {
	pane = {
		title = "Close Pane",
		run = function(ctx)
			check(rex.call("block.close", { session_id = ctx.session_id, block_id = ctx.block_id }))
		end,
	},
	tab = {
		title = "Close Tab",
		run = function(ctx)
			local window = panes.locate(ctx.block_id)
			check(rex.session.close_window({ window_id = window.window_id }))
		end,
	},
	session = {
		title = "Close Session",
		run = function(ctx)
			check(rex.call("session.destroy", { session_id = ctx.session_id }))
		end,
	},
}

local function define(what, key)
	local mode = "confirm-close-" .. what
	local action = "close_" .. what

	rex.action({ name = action, title = closers[what].title, run = closers[what].run })

	rex.mode(mode, { exclusive = true })
	prefix.bind(key, "client.mode.enter", { name = mode, once = true })
	rex.bind(mode .. "/y", action)
	rex.bind(mode .. "/n", "client.mode.exit")
end

define("pane", "x") -- x  kill-pane
define("tab", "shift+7") -- &  kill-window
define("session", "shift+x") -- X  kill-session
