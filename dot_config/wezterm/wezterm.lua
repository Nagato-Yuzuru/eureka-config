---
--- Created by colas.
--- DateTime: 2025/4/11 15:41
---
local wezterm = require("wezterm")

-- 创建配置对象
local config = {}

-- 使用新版本的配置构建器（如果可用）
if wezterm.config_builder then
	config = wezterm.config_builder()
end

config.use_ime = true
-- 外观设置
--config.color_scheme = "Dark Violet (base16)" -- 粉嫩
--config.color_scheme = "synthwave"
config.color_scheme = "iTerm2 Dark Background"
config.font = wezterm.font("JetBrainsMono Nerd Font", { weight = "Medium" })
config.colors = {
	cursor_bg = "#E98038", -- 鲜明的绿色
	-- 光标上文本的颜色
	cursor_fg = "#FFFFFF", -- 黑色文字
	-- 可选: 光标边框颜色
	cursor_border = "#52AD70",
}
config.font_size = 14.0

config.window_background_opacity = 0.95 -- 轻微透明效果
config.window_decorations = "RESIZE"
config.window_padding = {
	left = 5,
	right = 5,
	top = 5,
	bottom = 5,
}

-- 标签栏设置
config.tab_bar_at_bottom = true
config.use_fancy_tab_bar = true
config.hide_tab_bar_if_only_one_tab = true
config.show_tab_index_in_tab_bar = true

config.front_end = "WebGpu"
config.webgpu_power_preference = "HighPerformance"

-- 快捷键设置
config.keys = {
	-- 标签管理
	{ key = "o", mods = "CTRL|META", action = wezterm.action.ReloadConfiguration },

	-- { key = "t", mods = "SUPER",       action = wezterm.action.SpawnTab("CurrentPaneDomain") },
	{ key = "w", mods = "SUPER", action = wezterm.action.CloseCurrentTab({ confirm = true }) },
	{ key = "1", mods = "SUPER", action = wezterm.action.ActivateTab(0) },
	{ key = "2", mods = "SUPER", action = wezterm.action.ActivateTab(1) },
	{ key = "3", mods = "SUPER", action = wezterm.action.ActivateTab(2) },

	-- 窗格管理
	{ key = "%", mods = "SUPER|SHIFT", action = wezterm.action.SplitHorizontal({ domain = "CurrentPaneDomain" }) },
	{ key = '"', mods = "SUPER|SHIFT", action = wezterm.action.SplitVertical({ domain = "CurrentPaneDomain" }) },

	-- 窗格导航
	{ key = "h", mods = "SUPER|SHIFT", action = wezterm.action.ActivatePaneDirection("Left") },
	{ key = "l", mods = "SUPER|SHIFT", action = wezterm.action.ActivatePaneDirection("Right") },
	{ key = "k", mods = "SUPER|SHIFT", action = wezterm.action.ActivatePaneDirection("Up") },
	{ key = "j", mods = "SUPER|SHIFT", action = wezterm.action.ActivatePaneDirection("Down") },

	-- 字体大小调整
	{ key = "=", mods = "SUPER", action = wezterm.action.IncreaseFontSize },
	{ key = "-", mods = "SUPER", action = wezterm.action.DecreaseFontSize },
	-- { key = "-", mods = "CTRL|SHIFT", action = wezterm.action.SendString("\x1f") },
	{ key = "_", mods = "CTRL|SHIFT", action = wezterm.action.DisableDefaultAssignment }, -- 添加这个作为备选

	{ key = "0", mods = "SUPER", action = wezterm.action.ResetFontSize },
	-- 全屏切换
	{ key = "f", mods = "SUPER|SHIFT", action = wezterm.action.ToggleFullScreen },
}

-- 光标设置
config.default_cursor_style = "SteadyBlock"
--config.cursor_blink_rate = 800

-- 滚动设置
config.scrollback_lines = 10000
config.enable_scroll_bar = true

-- 默认工作目录
config.default_cwd = wezterm.home_dir

-- 启动时最大化窗口
config.initial_cols = 120
config.initial_rows = 30

-- 复制时自动复制到系统剪贴板
config.selection_word_boundary = " \t\n{}[]()\"'`,;:"
config.automatically_reload_config = true
-- 复制时自动复制到系统剪贴板 (OSC 52 支持)
config.set_environment_variables = {
	-- 告诉远程服务器支持 OSC 52 剪贴板
	TERM = "wezterm",
}

-- 鼠标设置
config.mouse_bindings = {
	-- 单击左键打开链接
	{
		event = { Up = { streak = 1, button = "Left" } },
		mods = "NONE",
		action = wezterm.action.CompleteSelection("ClipboardAndPrimarySelection"),
	},
	-- Ctrl+左键点击打开链接（改进版）
	{
		event = { Up = { streak = 1, button = "Left" } },
		mods = "CTRL",
		action = wezterm.action.OpenLinkAtMouseCursor,
	},
	-- Command+左键点击打开链接（Mac友好）
	{
		event = { Up = { streak = 1, button = "Left" } },
		mods = "SUPER",
		action = wezterm.action.OpenLinkAtMouseCursor,
	},
}

-- URL匹配规则（优化后）
config.hyperlink_rules = wezterm.default_hyperlink_rules()

local hyperlink_rules = {
	-- 1) 通用 URL (协议://...)
	{
		regex = [[\b(?:[a-zA-Z][a-zA-Z0-9+\-\.]*):\/\/(?:[^\s:@]+(?::[^\s:@]*)?@)?(?:[a-zA-Z0-9\-._~%]+|\[[a-fA-F0-9:.]+\])(?:\:\d{2,5})?(?:[\/?#][^\s]*)?\b]],
		format = "$0",
	},

	-- 2) 电子邮件
	{
		regex = [[\b[A-Za-z0-9_%+\-\.]+@[A-Za-z0-9._%\-]+\.[A-Za-z0-9._%\-]{2,63}\b]],
		format = "mailto:$0",
	},

	-- 3) Unix 文件路径
	-- 解释：匹配绝对路径 /foo/bar 或相对路径 ./foo/bar，排除单纯的斜杠
	{
		regex = [[\b(?:/(?:[\w.-]+(?:/[\w.-]+)*)|(?:\.\.?/)[\w.-]+(?:/[\w.-]+)*)\b]],
		format = "file://$0",
	},

	-- 4) IPv4 地址 (带可选端口)
	{
		regex = [[\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\:\d{1,5})?\b]],
		format = "http://$0",
	},
}

for _, rule in ipairs(hyperlink_rules) do
	table.insert(config.hyperlink_rules, rule)
end

return config
