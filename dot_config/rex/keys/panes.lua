-- Pane lookup over rex.session.view, shared by the pane and window commands.

local M = {}

-- Raise a failed Rex API call's error message; pass a successful result through.
function M.check(result, err)
	if err ~= nil then
		error(err, 0)
	end
	return result
end

-- Leaves of a layout tree, depth first: tmux's pane index order.
function M.collect(node, out)
	out = out or {}
	if node.block_id then
		out[#out + 1] = node.block_id
	elseif node.split then
		M.collect(node.split.before, out)
		M.collect(node.split.after, out)
	end
	return out
end

-- The window and layer holding block_id, that layer's panes in order, and
-- block_id's index among them.
function M.locate(block_id)
	local view = M.check(rex.session.view({}))
	for _, window in ipairs(view.windows) do
		for _, layer in ipairs(window.layers) do
			local panes = M.collect(layer.layout)
			for i, id in ipairs(panes) do
				if id == block_id then
					return window, layer, panes, i
				end
			end
		end
	end
	error("no window holds " .. tostring(block_id), 0)
end

return M
