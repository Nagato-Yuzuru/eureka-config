-- Resize mode: prefix then R, tap hjkl (shift for big steps), Esc or Enter to leave.

local prefix = require("keys.prefix")

rex.mode("resize", { exclusive = true })
prefix.bind("shift+r", "client.mode.enter", { name = "resize" })

for key, dir in pairs({ h = "left", j = "down", k = "up", l = "right" }) do
	rex.bind("resize/" .. key, "pane.resize", { direction = dir })
	rex.bind("resize/shift+" .. key, "pane.resize", { direction = dir, amount = 15 })
end
rex.bind("resize/=", "pane.balance")
rex.bind("resize/enter", "client.mode.exit")
