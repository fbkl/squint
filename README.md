# squint

Look at a camera through a keyhole.

`squint` renders an image topic into coloured terminal characters **on the
machine that owns the camera**, and publishes the few kilobytes of escape codes
instead of the few hundred kilobytes of pixels. You print them in a tmux pane.

It exists because five raw 640x480 streams were saturating a wifi link, and
because the only thing anyone actually needed from those streams was *"can the
camera see the marker right now"* — a question a 100x30 character picture
answers perfectly well.

```
camera ──► squint_node ──► AnsiFrame ──► squint_view ──► your terminal
  (on the pi, local, free)    (~30 kB)     (anywhere)
```

## Why two nodes

A node started by `roslaunch` on a remote machine has nowhere to print. Its
stdout goes to a log file nobody is watching. So the half that can see the
image and the half that has a screen are different processes on different
machines, and the escape codes travel between them as a normal ROS topic.

## Use

On the machine with the camera:

```bash
roslaunch squint squint.launch camera:=/thorax/cam0/image_raw name:=thorax
```

In a tmux pane, anywhere on the network:

```bash
rosrun squint squint_view _ns:=/thorax_squint
```

One pane per camera. Split the window and you are watching all of them.

The viewer reports its terminal size back to the renderer, so resizing the pane
resizes the picture — the image is scaled exactly once, at the source.

## What it costs

640x480 mono8 raw is **307 kB/frame**, 9.2 MB/s at 30 Hz, per subscriber.
Measured sizes for the same frame as 100x30 characters:

| source      | palette   | quantize | bytes/frame |
|-------------|-----------|----------|-------------|
| camera-ish  | truecolor | 0        | 74.5 kB     |
| camera-ish  | truecolor | 16       | 13.8 kB     |
| camera-ish  | **256**   | 0        | **14.2 kB** |
| mono noise  | truecolor | 0        | 94.5 kB     |
| mono noise  | **256**   | 0        | **29.9 kB** |

At the default 4 Hz and the 256-colour palette that is roughly **60–120 kB/s
per camera**, against 9.2 MB/s for the raw stream. Two orders of magnitude.

The renderer also does nothing at all when nobody is subscribed, so an idle
viewer costs the pi zero CPU.

## How it looks like a picture and not a smear

One character carries **two** pixels. The upper half block `▀` paints its top
half in the foreground colour and leaves the bottom half showing the
background, so a cell is two stacked pixels. Terminal cells are about twice as
tall as they are wide, which makes the result roughly square. Without this the
image comes out at half height and nothing is recognisable.

Downsampling is an exact rectangle average via an integral image, not a
resample — every source pixel contributes to exactly one character. A marker
one pixel wide still changes the colour of its cell.

## Bandwidth, honestly

A colour escape is only emitted when the colour **changes** along a row. That
is where most of the saving comes from, and it is why the 256-colour palette
wins twice: shorter escapes, and more neighbouring cells comparing equal.

`~quantize` buys more of the same at the cost of visible banding. Leave it at 0
unless you are desperate.

## Parameters

### `squint_node`

| param        | default | meaning |
|--------------|---------|---------|
| `~cols`      | 100     | character columns |
| `~rows`      | 30      | character rows |
| `~rate`      | 4.0     | renders per second |
| `~palette`   | `256`   | `256` or `truecolor` |
| `~quantize`  | 0       | round channels to a multiple of N |
| `~autosize`  | true    | obey a viewer's reported terminal size |
| `~max_cols`  | 400     | ceiling, so a bad resize cannot ask for a huge render |
| `~max_rows`  | 150     | ceiling |

Subscribes `image` (remap it), publishes `~ansi`, listens on `~size`.

### `squint_view`

| param         | default | meaning |
|---------------|---------|---------|
| `~ns`         | `""`    | namespace of the renderer, e.g. `/thorax_squint` |
| `~send_size`  | true    | report this terminal's size to the renderer |
| `~status`     | true    | bottom status line |

The status line shows the source topic, the character and pixel dimensions, the
measured rate, the **age of the frame** (source stamp to now — red past 250 ms)
and the size of the last frame. The age is the number worth watching: it is
true end-to-end latency, not a render time.

Piped rather than attached to a terminal, the viewer just dumps frames one
after another, which is useful for `squint_view | head -40`.

## Encodings

`mono8`, `mono16`, `bgr8`, `rgb8`, `rgba8`, `bgra8`, `8UC1`, `8UC3`.
No `cv_bridge`, no `cv2` — a pi already running a camera and a VIO pipeline
does not need another OpenCV in the loop.

Row stride (`msg.step`) is honoured, which matters on real hardware: drivers
pad rows, and a renderer that assumes `step == width * channels` produces a
diagonal smear on exactly the cameras you care about.
