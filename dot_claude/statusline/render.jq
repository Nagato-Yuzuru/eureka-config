# render.jq — jq module for Claude Code's `subagentStatusLine`.
# Contract: main($types; $base; $now) maps the stdin object to one
# {"id","content"} per task. The row body is a replica of Claude Code's default
# row ("type  description" … "elapsed · ↓ tokens") with the model's display name
# added to the right-hand stats cluster ("Haiku 4.5 · 6s · ↓ 15.6k tokens (8%)").
# When an inner renderer supplied $base[id], that body is kept and the model is
# appended instead (" · Haiku 4.5"), since we can't edit the middle of its row.
#   $types: {id: agentType}   from subagents/agent-<id>.meta.json
#   $base:  {id: content}     rows an inner renderer already produced
#   $now:   epoch seconds     injected so output is reproducible
# Source of truth: developed and golden-tested in ~/Fish/sg-status-line.

# Official display-name style: "claude-haiku-4-5-20251001" -> "Haiku 4.5",
# "claude-fable-5[1m]" -> "Fable 5 [1m]". Unknown shapes pass through; null -> "".
def model_label:
  if . == null then ""
  else (capture("^claude-(?<fam>[a-z]+)-(?<maj>[0-9]+)(?:-(?<min>[0-9]+))?(?:-[0-9]{8})?(?<suffix>\\[[^\\]]*\\])?$")
        | "\(.fam[:1] | ascii_upcase)\(.fam[1:]) \(.maj)\(if .min then ".\(.min)" else "" end)\(if .suffix then " \(.suffix)" else "" end)")
       // .
  end;

# What gets appended to an inner renderer's row.
def model_suffix: model_label | if . == "" then "" else " · \(.)" end;

# Terminal display width: codepoints + 1 extra per East Asian wide char.
def width:
  length + ([scan("[\\p{Han}\\p{Hangul}\\p{Hiragana}\\p{Katakana}\\x{FF00}-\\x{FF60}\\x{FFE0}-\\x{FFE6}]")] | length);

# Truncate to display width $w, ending in "…" when anything was cut.
def fit($w):
  if width <= $w then .
  elif $w <= 0 then ""
  else (explode | map([.] | implode)) as $chars
    | reduce $chars[] as $c ({s: "", w: 0, done: false};
        if .done then .
        else ($c | width) as $cw
          | if .w + $cw <= $w - 1 then .s += $c | .w += $cw else .done = true end
        end)
    | .s + "…"
  end;

def pad2: tostring | if length < 2 then "0" + . else . end;

# startTime (epoch ms) -> "6s" | "1m05s" | "1h02m"
def elapsed($now):
  if . == null then "" else
    ([($now - . / 1000) | floor, 0] | max) as $s
    | if $s < 60 then "\($s)s"
      elif $s < 3600 then "\($s / 60 | floor)m\($s % 60 | pad2)s"
      else "\($s / 3600 | floor)h\(($s % 3600) / 60 | floor | pad2)m" end
  end;

# 612 | 15.6k | 138k | 1.2M
def tokens:
  (. // 0) as $n
  | if $n < 1000 then "\($n)"
    elif $n < 100000 then "\(($n / 100 | floor) / 10)k"
    elif $n < 1000000 then "\($n / 1000 | floor)k"
    else "\(($n / 100000 | floor) / 10)M" end;

# Default row body with the model in the stats cluster, right-aligned into $w columns.
def default_row($w; $type; $now):
  (if $type then "\($type)  " else "" end) as $lead
  | (.description // .label // "") as $desc
  | (.tokenCount // 0) as $tc
  | ("↓ \($tc | tokens) tokens"
     + (if .contextWindowSize then " (\(($tc * 100 / .contextWindowSize) | floor)%)" else "" end)) as $tok
  | ([(.model | model_label), (.startTime | elapsed($now)), $tok] | map(select(. != "")) | join(" · ")) as $right
  | ($desc | fit($w - ($lead | width) - ($right | width) - 1)) as $d
  | ([$w - ($lead | width) - ($d | width) - ($right | width), 1] | max) as $pad
  | $lead + $d + (" " * $pad) + $right;

# Inner renderers see the width that remains after the widest model suffix.
def shrink_columns:
  ([.tasks[]? | .model | model_suffix | width] | max // 0) as $sw
  | .columns = ((.columns // 80) - $sw);

def main($types; $base; $now):
  (.columns // 80) as $cols
  | .tasks[]?
  | {id, content: (if $base[.id] != null then $base[.id] + (.model | model_suffix)
                   else default_row($cols; $types[.id]; $now) end)};
