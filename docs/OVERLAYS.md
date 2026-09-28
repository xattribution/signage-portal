# Stream banners, graphics and motion

This revision adds a presentation layer to the existing web player. A stream's top ribbon, bottom ribbon and transition settings are independent of its rotation. A message edit does not rebuild the playlist or recreate an unchanged video. It does not change any display or stream URL.

## Use the editor

Open **Streams → Banners & motion** on the stream to change. The monitor is a live draft preview using the same renderer as the player, over a thumbnail of the stream's content. It is not a live recording of the physical display. **Apply to stream** publishes the settings; **Cancel** discards the draft. **Disable both ribbons** changes the draft, so Apply is still required.

The top and bottom ribbons are independent. Enable either, both, or neither. A fixed message above a scrolling announcement below is a normal supported configuration. Changes reach loaded players on their existing state poll, normally about five seconds; configured start/end boundaries are also evaluated locally.

| Control | Behavior |
|---|---|
| Static text | A single line. Overflow is ellipsized, and the editor warns when it detects clipping. |
| Scrolling text | Leftward or rightward travel, with speed adjustable from 15–240 reference pixels/second at 1080p. It scales with screen height. |
| Clock | Optional label, IANA time zone, 12/24-hour format, seconds and date. |
| Countdown | Optional label and an absolute target. At zero, show a finished message, hold at zero, or hide the ribbon. |
| Appearance | Independent background/text colors, background opacity, alignment, graphic position, ribbon height and type size. |
| Timing | Optional start and end time per ribbon. Use an end time for temporary exercise/status notices. |
| Entrance | None, fade or slide; 0–1,500 ms. This runs when a ribbon becomes visible, not on every poll or slide. |

Height is 3–24% of the screen per ribbon. Type size is a screen-height percentage, limited to 80% of its ribbon height. Graphics fit inside the band without stretching. The editor warns about low text contrast and translucent backgrounds; warnings do not guarantee readability over every possible slide.

Text is plain text, not HTML, CSS, Markdown or a template language. For example, `DEFCON 3` or `EX EX EX` is exactly the operator-entered message, not an automatically obtained status or official alert. The portal is not a monitored emergency-notification system. No status is enabled by default.

### Cover content, or reserve space

Under **Layout & motion**, **Reserve space for ribbons** fits the content between the currently visible bands. Images and converted slides retain their aspect ratio, which can introduce letterboxing. This is the preferred setting when slide headers/footers must remain visible. With it disabled, ribbons cover the top/bottom of the full-screen content.

External web pages can lay themselves out differently at the reduced viewport size; test the actual page. A display set to **Off** remains black, including overlays. An empty stream with Blank fallback may still show its own enabled ribbons.

### Clocks and countdowns

Ribbon clocks have their own explicit time zones, initially UTC. Choose a zone such as `Pacific/Honolulu`, rather than assuming the device's clock matches the administrator's browser. Screensaver **Clock artwork → Clock time zone** optionally sets the built-in clock's zone too; blank preserves the previous device-local behavior. Its 12/24-hour convention continues to follow `CLOCK_24H`.

Countdown targets and optional start/end dates are entered in the **administrator browser's local time zone** and saved as UTC epoch timestamps. Restarting the server or player does not restart a countdown. A loaded player evaluates expiry locally even while its next poll fails. Accurate host time, the player's clock and successful clock synchronization still matter; a wrong clock or a unavailable/rebooting display is not solved by durable settings.

## Save reusable presets

**Save as preset** stores a named copy of both ribbons, layout, motion and screensaver artwork. Presets are shared with the other authenticated gatekeepers and survive service restarts. At most 100 are allowed.

**Load** copies a preset into the current draft; it does not immediately publish. The editor clears old start/end dates and countdown targets when loading, so last week's exercise deadline cannot silently become this week's timer. Set a new countdown target before applying an enabled countdown.

Applying or changing a stream does not mutate a saved preset. Deleting a preset does not change streams that previously copied it. Presets are not live-linked global alerts: apply them to each desired stream. Concurrent stream edits use a revision check; reopen on a conflict rather than overwriting another editor's work.

## Add PNG, SVG or GIF graphics

Use **Library → Add graphic**, or **Upload graphic** in the editor. In the editor, **Refresh graphics** refreshes the ready list after conversion. In **Shared folders**, choose **As graphic** for a PNG/GIF/SVG on an approved NAS folder. Imports snapshot the file rather than following future NAS changes.

This is deliberately distinct from **Upload content**. The existing content path continues to make predictable full-screen JPEG/video renditions and does not promise retained PNG transparency or GIF motion. Use the graphic path when alpha or animation matters.

| Input | Published result and limits |
|---|---|
| PNG | Re-encoded, metadata-free PNG, preserving alpha. Input up to 32 MiB and 8 megapixels; output fits within 2,048 × 2,048. Animated PNG is rejected. |
| SVG | A self-contained static SVG up to 2 MiB is validated and rasterized to a transparent PNG within 2,048 × 2,048. Raw SVG is never a player response. |
| GIF | Re-encoded animation, up to 32 MiB input, 8 megapixels per input frame, 120 frames, and 60 seconds per normalized loop. Output fits within 1,280 × 1,280, with at most 32 million aggregate output-frame pixels. |

GIF frame delays normalize to 50–10,000 ms (at most 20 fps); the cleaned GIF loops indefinitely. Palette conversion and GIF's binary transparency can alter subtle edges/colors. Single-frame inputs become PNG. A static JPEG poster is generated for previews and reduced-motion fallback. Graphic output is capped at 20 MiB. These are logo/ribbon-animation limits, not a substitute for the video pipeline.

SVG supports a deliberately small subset: paths, basic shapes, groups, gradients, clipping, text and simple presentation properties. It rejects scripts, events, `foreignObject`, linked/embedded images, `use` references, external resources, DTD/entities, stylesheet instructions, animation and unsupported elements/attributes. A deny-all resource loader is applied during rendering as well. For an unsupported logo, export a plain path-based SVG or a transparent PNG. SVG fonts are not bundled; convert text to paths when exact appearance matters. SVG animation is not retained.

The original file stays in the protected originals store and can be exported through the authenticated NAS workflow. Only the normalized raster output is shown on a player. A graphic referenced by a stream or preset cannot be deleted until those references are cleared, even in disabled ribbons.

### Screensaver use

For a graphic **beside the built-in clock**, open the Screensaver's editor and choose **Clock artwork**. Choose a width, opacity and center/corner position. It shows when the Screensaver rotation is empty and the built-in clock is active.

For **full-screen artwork or animated GIF content**, use its Library **Place** action and select the Screensaver rotation. Normal streams falling back to the Screensaver inherit its clock artwork and optional clock time zone, but keep their own top/bottom ribbons. A player assigned directly to the Screensaver uses that stream's own ribbons.

Native GIF frame phase is not synchronized between displays. Tickers and countdowns use the shared estimated clock, but this is not frame-locked broadcast graphics.

## Transitions and reduced motion

Choose **Cut**, **Crossfade**, **Slide left** or **Slide up**, with a duration of 0–2,000 ms. The actual duration is limited to half the current item's dwell time. Slide dwell still belongs to the rotation/placement settings; transition duration is not an extra hold time.

The incoming slide animates over the outgoing frame. The outgoing element is released after the transition; it does not accumulate as an invisible slideshow history. Tickers animate with transforms, and crossfades use opacity. Unsupported animation APIs fall back to a cut/static text.

**Reduce motion on this stream**, or the device/browser's reduced-motion preference, stops scrolling and transition/entrance animation. Animated graphics use the generated still poster, including when a graphic occupies a full-screen rotation slot. A JPEG poster does not retain transparency; choose static PNG artwork where transparent reduced-motion appearance is essential.

These are transitions between player items. PowerPoint's internal object animations, embedded media and transition effects are still lost in the existing slide-to-image conversion. Export an animated presentation to a video and upload that video when those effects must be retained.

## Persistence, deployment and scope

Ribbon settings, preset copies, revisions, absolute times and the NAS import's graphic/content choice live in persistent local SQLite. Graphic originals and renditions use the existing NAS/media storage. Keep the same database, mount paths, volume markers and DNS names; no playback URL replacement is needed.

Upgrade **web and worker together** after backing up and testing. The worker now also installs CairoSVG/defusedxml and Cairo's runtime library. The playback reverse proxy must allow `/static/overlay.js` and `/static/overlay.css`; the updated example includes these. The player remains view-only and all editor/graphic/preset mutations use the existing authentication and write guard.

Reload already-open player pages once after upgrading the software so they load the new player assets; the address itself stays the same. Subsequent ribbon edits use normal manifest polling. A refresh/reboot still needs reachable server assets or an effective browser cache; this is not a complete offline-media package.

The overlay is composited by the **HTML player**, over images, converted slides, video and browser-compatible live feeds. It is not burned into an RTSP/HLS relay's raw output, not a native UniFi OS overlay, and not visible to an RTSP-only device bypassing this player. Full composited-channel video encoding remains outside this release.

## Added authenticated API

```text
GET    /api/streams/{id}/presentation
PUT    /api/streams/{id}/presentation     {expected_revision, settings}
GET    /api/presentation/presets
POST   /api/presentation/presets          {name, settings}
DELETE /api/presentation/presets/{id}
POST   /api/graphics                     multipart: file, optional title
POST   /api/shares/{id}/import            existing body + as_graphic: true
```

The public player state contains only resolved raster URLs and settings, with a separate `presentation.version`. Presentation-only edits do not alter the playlist `version`. `presentation_change_ms` is separate from the content schedule edge, so a ribbon end time cannot accidentally end a cached content override.

See [OVERLAY-VALIDATION.md](OVERLAY-VALIDATION.md) for executed checks and remaining deployment gates; [PERSISTENCE.md](PERSISTENCE.md) and [UPGRADING.md](UPGRADING.md) remain required reading before installation.

### Primary implementation references

[CairoSVG documentation](https://cairosvg.org/documentation/) describes raster output and its unsafe XML option (not enabled here). [Pillow's format handbook](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html) documents GIF frames, disposal and transparency. [Browser animation guidance](https://web.dev/articles/animations-guide) explains the transform/opacity approach. These describe tools, not a security certification of this portal.

The web image explicitly installs the system `tzdata` package for IANA time-zone validation. This is clock data, not a media parser. Standalone installations must also provide an IANA time-zone database. This image change still requires the documented clean build.
