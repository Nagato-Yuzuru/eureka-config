-- The tmux-style prefix. Every prefix binding goes through this module.
-- Keys are written unshifted: "shift+-" is "_", "shift+=" is "+".

local M = {}

M.key = "alt+'"

function M.bind(key, action, args)
	rex.bind(M.key .. ">" .. key, action, args)
end

return M
