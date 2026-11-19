-- prefix ; → tmux 的 command-prompt + display-popup：先输入命令，再弹浮窗跑。
-- - 例：输入 "lazygit" / "btop" / "nvim ." / "git log --oneline -20"
-- - 空输入直接 Enter → 浮窗打开 $SHELL
-- - Esc / Ctrl-C → 取消，什么都不开
-- - 命令正常退出（或 Ctrl-C 掉）→ 浮窗关闭
-- - 命令秒失败 → 不出现任何浮窗，输入条原地显示报错，原命令保留，可改了重跑
--
-- Rex 还没有 app 级的文本输入（Lua 侧无 ui.input）和可编程的 toast，所以输入条是
-- 一个矮的浮动终端，跑 zsh -f（不读 rc，近乎瞬开）。回车后：
-- 1. popup_run 在 detached block（不属于任何窗口，界面上不存在）里起命令；
-- 2. 命令撑过 SHOW_DELAY，popup_show 把它放进窗口并浮起来，经 fifo 告诉输入条
--    "shown"，输入条退出；
-- 3. 撑不过就失败了：block 把屏幕末尾几行报错经 fifo 交给输入条，输入条原地显示。
-- 显示之后才失败的命令（跑了一会儿再报错）走 popup_failed：浮窗关，重开输入条。

local prefix = require("keys.prefix")
local panes = require("keys.panes")

local SIZE = 0.8 -- 浮窗宽高各占窗口 80%，同 tmux 配置的 -w 80% -h 80%
local BAR = { 0.15, 0.84, 0.7, 0.06 } -- 输入条：靠下居中，约 3 行
local BAR_WITH_ERROR = { 0.15, 0.78, 0.7, 0.12 } -- 多留几行给报错
local SHOW_DELAY = "0.3" -- 命令跑过这么久（秒）才显示浮窗
local TERMINAL = "com.superlogical.terminal"
local SHELL_FLAVOR = "com.superlogical.terminal.shell"

-- 程序一退出就关，不走 Rex "快速失败保留窗口" 的启发式；错误由脚本自己处理。
local CLOSE_ON_EXIT = { on_completion = true, quick_exit_threshold_ms = 0 }

-- 输入条。$1 预填的命令，$2 初始报错（可空）。等结果时阻塞在 fifo 上。
local BAR_SCRIPT = [=[
fifo=${TMPDIR:-/tmp}/rex-popup.$$
trap 'rm -f $fifo $fifo.shown' EXIT
trap 'exit 0' INT
mkfifo $fifo && exec {fd}<>$fifo || { print -r -- "popup：无法创建 fifo $fifo"; read -sk1; exit 1 }
# Esc 取消：放一个哨兵再正常提交。不用 send-break——它会中止整个脚本，EXIT trap 不跑，fifo 泄漏。
cancel() { BUFFER=$'\x18'; zle accept-line }
zle -N cancel
bindkey '^[' cancel
REPLY=$1 err=$2
while :; do
	print -n '\e[H\e[2J'   # 清屏（也清掉 login(1) 的 "Last login" 横幅）
	[[ -n $err ]] && print -r -- $'\e[31m'"$err"$'\e[0m'
	vared -p '%F{blue}popup>%f ' REPLY || exit 0
	[[ $REPLY == $'\x18' ]] && exit 0
	if ! out=$(rex do popup_run "command=$REPLY" "cwd=$PWD" "bar=$REX_BLOCK" "fifo=$fifo" 2>&1 >/dev/null); then
		err="popup 打开失败：$out"
		continue
	fi
	if ! IFS= read -r -d '' -t 60 -u $fd res; then
		err="popup：60 秒内没有收到命令的结果"
		continue
	fi
	[[ $res == shown || $res == done ]] && exit 0
	err=$res
done
]=]

-- 命令本体，在 detached block 里跑。zsh -i：读 .zshrc，让别名和 mise 的 PATH 跟
-- 普通 pane 一致。$1 命令，$2 显示延迟，$3 输入条 block，$4 fifo。
local RUN_SCRIPT = [=[
setopt extendedglob
cmd=$1 delay=$2 bar=$3 fifo=$4 flag=$4.shown
show() { rex do popup_show "block=$REX_BLOCK" "bar=$bar" >/dev/null 2>&1 }
report() { [[ -p $fifo ]] && print -rn -- "$1"$'\0' > $fifo }
if [[ -z $cmd ]]; then show; report shown; exec "$SHELL" -l; fi
dir=$PWD
print -n '\e[H\e[2J\e[3J' # 清屏 + 清滚动区：.zshrc 的横幅不进报错摘录
{ sleep $delay; : > $flag; show; report shown } &!
shower=$!
( eval "$cmd" ) # 子 shell：命令里的 exit 只退出它，后面的收尾照常跑
rc=$?
kill $shower 2>/dev/null
if [[ ! -e $flag ]]; then   # 从未显示过
	(( rc == 0 || rc == 130 )) && { report done; exit 0 }
	lines=(${(f)"$(rex capture -b $REX_BLOCK 2>/dev/null)"})
	lines=(${lines:#[[:space:]]#})
	msg=${(F)lines[$(( ${#lines} > 3 ? ${#lines} - 2 : 1 )),-1]} # 末尾 3 行；不足 3 行时 [-3,-1] 会返回空
	rex do popup_bar_error "bar=$bar" >/dev/null 2>&1
	report "${msg:-[exit $rc]}"
	exit 0
fi
rm -f $flag
(( rc == 0 || rc == 130 )) && exit 0
rex do popup_failed "command=$cmd" "cwd=$dir" "rc=$rc" "block=$REX_BLOCK" >/dev/null 2>&1 && exit 0
print -P "\n%F{red}[exit $rc]%f 按任意键关闭"
read -sk1
]=]

-- 终端 pwd 是 file://HOST/PATH 形式的 URL；取出并解码 PATH。
local function focused_cwd(block_id)
	local result = rex.block.call(TERMINAL, "pwd", { block_id = block_id })
	local path = result and result.pwd and result.pwd:match("^file://[^/]*(/.*)$")
	if path == nil then
		return nil
	end
	return (path:gsub("%%(%x%x)", function(hex)
		return string.char(tonumber(hex, 16))
	end))
end

-- 终端屏幕最后 n 行非空行，去掉行尾空白。
local function tail_lines(block_id, n)
	local result = rex.block.call(TERMINAL, "format", {
		block_id = block_id,
		format = "text",
		trim = true,
	})
	local lines = {}
	for line in ((result and result.content) or ""):gmatch("[^\n]+") do
		line = line:gsub("%s+$", "")
		if line ~= "" then
			lines[#lines + 1] = line
		end
	end
	return table.concat(lines, "\n", math.max(1, #lines - n + 1))
end

local function centered()
	local offset = (1 - SIZE) / 2
	return { offset, offset, SIZE, SIZE }
end

-- 窗口平铺层里的任意一个 pane，作为放置浮窗的锚点。
local function tiled_anchor(window)
	for _, layer in ipairs(window.layers) do
		if layer.kind == "tiled" then
			local ids = panes.collect(layer.layout)
			if ids[1] then
				return ids[1]
			end
		end
	end
	error("当前窗口没有平铺的 pane，无法放置浮窗", 0)
end

-- 把一个 detached block 放进 window，浮起来并聚焦。API 只能先作为分屏放入
-- 再 float，所以显示瞬间可能有一帧分屏。
local function place_floating(block_id, window, bounds)
	panes.check(rex.session.move_block({
		block_id = block_id,
		anchor_block_id = tiled_anchor(window),
		direction = "vertical",
	}))
	panes.check(rex.session.float_block({ block_id = block_id, bounds = bounds }))
	panes.check(rex.session.focus_block({ block_id = block_id }))
end

local function open_bar(cwd, command, err)
	panes.check(rex.session.new_layer({
		bounds = err and BAR_WITH_ERROR or BAR,
		layout = {
			block = {
				flavor = SHELL_FLAVOR,
				label = "popup>",
				options = {
					shell = "none",
					command = { "/bin/zsh", "-f", "-c", BAR_SCRIPT, "popup", command or "", err or "" },
					cwd = cwd,
					exit = CLOSE_ON_EXIT,
				},
			},
		},
		focus = true,
	}))
end

rex.action({
	name = "popup",
	title = "Popup Command…",
	run = function(ctx)
		open_bar(ctx.block_id and focused_cwd(ctx.block_id) or nil)
	end,
})

-- 由输入条调用：在 detached block 里起命令，尺寸按输入条估算成浮窗大小，
-- 避免显示时再整体重排。
rex.action({
	name = "popup_run",
	title = "Popup Run",
	args = { command = "string?", cwd = "string?", bar = "string", fifo = "string" },
	run = function(_, args)
		local _, bar_layer = panes.locate(args.bar)
		local bar_size = rex.block.call(TERMINAL, "size", { block_id = args.bar })
		local size = nil
		if bar_size and bar_size.columns then
			size = {
				columns = math.floor(bar_size.columns / bar_layer.bounds[3] * SIZE),
				rows = math.max(10, math.floor(bar_size.rows / bar_layer.bounds[4] * SIZE)),
			}
		end
		panes.check(rex.session.new_block({
			flavor = SHELL_FLAVOR,
			label = "popup",
			options = {
				shell = "none",
				command = {
					"/bin/zsh",
					"-lic",
					RUN_SCRIPT,
					"popup",
					args.command or "",
					SHOW_DELAY,
					args.bar,
					args.fifo,
				},
				cwd = args.cwd,
				size = size,
				exit = CLOSE_ON_EXIT,
			},
		}))
	end,
})

-- 由命令 block 调用：命令撑过 SHOW_DELAY，放进输入条所在窗口并显示。
rex.action({
	name = "popup_show",
	title = "Popup Show",
	args = { block = "string", bar = "string" },
	run = function(_, args)
		local window = panes.locate(args.bar)
		place_floating(args.block, window, centered())
	end,
})

-- 由命令 block 在秒失败时调用：把输入条加高，腾出放报错的行。
rex.action({
	name = "popup_bar_error",
	title = "Popup Bar Error",
	args = { bar = "string" },
	run = function(_, args)
		local _, layer = panes.locate(args.bar)
		panes.check(rex.session.resize_layer({ layer_id = layer.layer_id, bounds = BAR_WITH_ERROR }))
	end,
})

-- 由已显示的浮窗在命令失败时调用：读它屏幕上的报错，重开带报错和原命令的输入条。
-- 浮窗随后自行退出关闭。
rex.action({
	name = "popup_failed",
	title = "Popup Failed",
	args = { command = "string", cwd = "string?", rc = "integer", block = "string" },
	run = function(_, args)
		local err = tail_lines(args.block, 3)
		open_bar(args.cwd, args.command, err ~= "" and err or ("[exit " .. args.rc .. "]"))
	end,
})

prefix.bind(";", "popup")

-- prefix ` → 常驻 scratch 浮窗的开关：每个 session 一个，按 label 认。
-- - 没有 → 在当前目录新建一个 shell
-- - 在当前窗口：显示着且焦点在它身上 → 最小化（进程继续跑）；
--   被最小化，或显示着但焦点不在它身上 → 恢复、置顶并聚焦
-- - 在别的窗口（不论是否最小化）→ 搬到当前窗口显示，沿用上次的位置大小
-- 里面的 shell exit 后它就真的关了，下次再按会重建。

local SCRATCH = "scratch"

-- session 里的 scratch：{ block_id, window, layer }。浮着的带 window/layer，
-- 被 detach 的只有 block_id。focused 用来判断焦点是否在它的层里。
local function find_scratch(view, focused)
	for _, window in ipairs(view.windows) do
		for _, layer in ipairs(window.layers) do
			if layer.kind ~= "tiled" then
				local found, inside = nil, false
				for _, block in ipairs(layer.blocks or {}) do
					if block.label == SCRATCH then
						found = block.block_id
					end
					inside = inside or block.block_id == focused
				end
				if found then
					return { block_id = found, window = window, layer = layer }, inside
				end
			end
		end
	end
	local listed = panes.check(rex.session.list_blocks({}))
	for _, block in ipairs(listed.blocks or {}) do
		if block.label == SCRATCH and block.placement == "detached" then
			return { block_id = block.block_id }, false
		end
	end
	return nil, false
end

-- 把 scratch 从别的窗口（或 detached 状态）搬进 window，浮起来并聚焦。
local function bring_scratch(scratch, window, bounds)
	if scratch.window then
		panes.check(rex.session.detach_block({ block_id = scratch.block_id }))
	end
	place_floating(scratch.block_id, window, bounds)
end

rex.action({
	name = "scratch_toggle",
	title = "Toggle Scratch Popup",
	run = function(ctx)
		local window = panes.locate(ctx.block_id)
		local view = panes.check(rex.session.view({}))
		local scratch, inside = find_scratch(view, ctx.block_id)
		local offset = (1 - SIZE) / 2
		local default_bounds = { offset, offset, SIZE, SIZE }

		if scratch == nil then
			panes.check(rex.session.new_layer({
				window_id = window.window_id,
				bounds = default_bounds,
				layout = {
					block = {
						flavor = "com.superlogical.terminal.shell",
						label = SCRATCH,
						options = { cwd = focused_cwd(ctx.block_id) },
					},
				},
				focus = true,
			}))
		elseif scratch.window == nil or scratch.window.window_id ~= window.window_id then
			bring_scratch(scratch, window, scratch.layer and scratch.layer.bounds or default_bounds)
		elseif scratch.layer.state ~= "minimized" and inside then
			panes.check(rex.session.set_layer_state({ layer_id = scratch.layer.layer_id, state = "minimized" }))
		else
			panes.check(
				rex.session.set_layer_state({ layer_id = scratch.layer.layer_id, state = "normal", focus = true })
			)
		end
	end,
})

prefix.bind("`", "scratch_toggle")
