# Running the orb on Sway

Wayland does not let an app place its own window, so on Sway the orb opens tiled, or centred if it is
floating, whatever `[gui] position` says. A window rule in your Sway config fixes that. `./install.sh`
prints this rule with the numbers for your screen; add it to `~/.config/sway/config`:

```
# voice-instinct orb: float, show on every workspace, no border, top-right under the bar
for_window [app_id="voice-instinct"] floating enable, sticky enable, border none, resize set 128 128, move position 1128 12
# optional: show or hide the orb from the keyboard
bindsym $mod+n exec voice-instinct toggle
# start the orb at login (Sway does not run XDG autostart entries; skip this if your config
# already has `exec dex --autostart`, which runs them)
exec voice-instinct
```

Then run `swaymsg reload`. The rule applies the next time the orb appears.

- `resize set` should match `[gui] size` in `config.toml` (default 128).
- `move position X Y` is measured from the top-left of the area under your bar. For the top-right
  corner with a 24 px margin, X = screen width − size − 24 (1280 − 128 − 24 = 1128 on a 1280 px
  wide screen) and Y = 12.
- Sway applies the rule every time the orb appears, so the orb always comes back to this position.
