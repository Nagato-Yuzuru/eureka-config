-- Prefix bindings for panes, tabs, and sessions.

local prefix = require("keys.prefix")
local bind = prefix.bind

-- Press the prefix twice to send it to the terminal
bind(prefix.key, "pane.send_key", { key = prefix.key })

-- Panes
bind("-", "pane.split", { direction = "down" })
bind("shift+-", "pane.split", { direction = "right" })
bind("shift+'", "pane.split", { direction = "down" }) -- "  split-window
bind("shift+5", "pane.split", { direction = "right" }) -- %  split-window -h
bind("h", "pane.focus.left")
bind("j", "pane.focus.down")
bind("k", "pane.focus.up")
bind("l", "pane.focus.right")
-- tmux.conf.local rebinds prefix C-hjkl to select-pane (vim-tmux-navigator),
-- overriding Oh my tmux!'s prefix C-h/C-l window cycling
bind("ctrl+h", "pane.focus.left")
bind("ctrl+j", "pane.focus.down")
bind("ctrl+k", "pane.focus.up")
bind("ctrl+l", "pane.focus.right")
bind("z", "pane.zoom")
bind("shift+=", "pane.zoom")
bind("=", "pane.balance")
bind("shift+2", "pane.move_to_tab") -- @  join-pane
bind("shift+1", "pane.move_to_new_tab") -- !  break-pane

-- Tabs (tmux windows)
bind("c", "client.tab.new")
bind("ctrl+n", "client.tab.next")
bind("ctrl+p", "client.tab.previous")
bind(",", "client.tab.rename")
for i = 1, 9 do
	bind(tostring(i), "client.tab.goto", { index = i })
end

-- Sessions
bind("ctrl+c", "session.new")
bind("o", "session.switch")
bind("s", "session.switch")
bind("ctrl+f", "session.switch")
bind("shift+4", "session.rename") -- $

-- Misc
bind("shift+;", "client.palette.toggle") -- :
bind("/", "client.find.open")
bind("r", "client.config.reload")
