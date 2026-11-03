-- tmux swap-pane -D / -U: swap the focused pane with the next or previous one
-- in pane order, wrapping around. Rex only swaps two named blocks, so the
-- order is read from the layout tree.

local prefix = require("keys.prefix")
local panes = require("keys.panes")

rex.action({
	name = "pane_swap",
	title = "Swap Pane with Next or Previous",
	args = { direction = "string" },
	run = function(ctx, args)
		local step = ({ next = 1, previous = -1 })[args.direction]
		if not step then
			error("direction must be next or previous", 0)
		end
		local _, _, order, i = panes.locate(ctx.block_id)
		if #order < 2 then
			return
		end
		local other = order[(i - 1 + step) % #order + 1]
		panes.check(rex.session.swap_blocks({ block_id = ctx.block_id, other_block_id = other }))
	end,
})

prefix.bind("shift+.", "pane_swap", { direction = "next" }) -- >  swap-pane -D
prefix.bind("shift+,", "pane_swap", { direction = "previous" }) -- <  swap-pane -U
