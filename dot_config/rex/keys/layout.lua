-- tmux select-layout presets for the focused window's tiled panes:
-- prefix M-1..M-5 picks one, prefix M-Space cycles (next-layout).
--
-- Rex has no layout presets, so a layout is rebuilt: every pane but the
-- first is detached, then each is placed back with move_block beside the
-- first pane of its sibling subtree. Placing into a whole slot never
-- collapses a split already built. Pane order is kept.

local prefix = require("keys.prefix")
local panes = require("keys.panes")
local check = panes.check

-- Share of the window the main pane takes in main-horizontal/main-vertical.
local MAIN_SHARE = 0.6

-- A layout tree is { leaf = block_id } or
-- { dir = "horizontal" | "vertical", ratio = share of before, before, after }.

local function leaves(ids, from, to)
	local out = {}
	for i = from or 1, to or #ids do
		out[#out + 1] = { leaf = ids[i] }
	end
	return out
end

-- nodes side by side (horizontal) or stacked (vertical), equal shares.
local function chain(dir, nodes, i)
	i = i or 1
	if i == #nodes then
		return nodes[i]
	end
	return { dir = dir, ratio = 1 / (#nodes - i + 1), before = nodes[i], after = chain(dir, nodes, i + 1) }
end

local function main(dir, rest_dir)
	return function(ids)
		return { dir = dir, ratio = MAIN_SHARE, before = { leaf = ids[1] }, after = chain(rest_dir, leaves(ids, 2)) }
	end
end

-- tmux's grid: grow rows, then columns, until every pane fits; fill row by row.
local function tiled(ids)
	local rows, cols = 1, 1
	while rows * cols < #ids do
		rows = rows + 1
		if rows * cols < #ids then
			cols = cols + 1
		end
	end
	local row_nodes = {}
	for first = 1, #ids, cols do
		row_nodes[#row_nodes + 1] = chain("horizontal", leaves(ids, first, math.min(first + cols - 1, #ids)))
	end
	return chain("vertical", row_nodes)
end

-- In tmux's next-layout order.
local layouts = {
	{ name = "even-horizontal", build = function(ids) return chain("horizontal", leaves(ids)) end },
	{ name = "even-vertical", build = function(ids) return chain("vertical", leaves(ids)) end },
	{ name = "main-horizontal", build = main("vertical", "horizontal") },
	{ name = "main-vertical", build = main("horizontal", "vertical") },
	{ name = "tiled", build = tiled },
}

local function index_of(name)
	for i, layout in ipairs(layouts) do
		if layout.name == name then
			return i
		end
	end
	error("unknown layout " .. tostring(name), 0)
end

local function first_leaf(node)
	while not node.leaf do
		node = node.before
	end
	return node.leaf
end

local function place(node)
	if node.leaf then
		return
	end
	check(rex.session.move_block({
		block_id = first_leaf(node.after),
		anchor_block_id = first_leaf(node.before),
		direction = node.dir,
		side = "after",
		ratio = node.ratio,
	}))
	place(node.before)
	place(node.after)
end

-- Layout index last applied per window, for next-layout.
local applied = {}

local function apply(ctx, index)
	local window, layer, order = panes.locate(ctx.block_id)
	if layer.kind ~= "tiled" then
		error("layouts apply to tiled panes, not floating ones", 0)
	end
	applied[window.window_id] = index
	if #order < 2 then
		return
	end
	for i = 2, #order do
		check(rex.session.detach_block({ block_id = order[i] }))
	end
	place(layouts[index].build(order))
	check(rex.session.focus_block({ block_id = ctx.block_id }))
end

rex.action({
	name = "layout_select",
	title = "Select Layout",
	args = { name = "string" },
	run = function(ctx, args)
		apply(ctx, index_of(args.name))
	end,
})

rex.action({
	name = "layout_next",
	title = "Next Layout",
	run = function(ctx)
		local window = panes.locate(ctx.block_id)
		apply(ctx, (applied[window.window_id] or 0) % #layouts + 1)
	end,
})

for i, layout in ipairs(layouts) do
	prefix.bind("alt+" .. i, "layout_select", { name = layout.name }) -- M-1..M-5  select-layout
end
prefix.bind("alt+space", "layout_next") -- M-Space  next-layout
