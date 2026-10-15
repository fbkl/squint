# One rendered frame, ready to be dumped straight onto a terminal.
#
# header.stamp    is the stamp of the SOURCE IMAGE, not the render time, so a
#                 viewer can show the true end-to-end age of what you are seeing.
# header.frame_id is the resolved name of the source image topic.
Header header

uint16 cols         # character columns in the render
uint16 rows         # character rows in the render
uint16 src_width    # source image width, in pixels
uint16 src_height   # source image height, in pixels
string encoding     # source image encoding, e.g. mono8

# The block itself: `rows` lines separated by '\n', no trailing newline.
# Each line ends with a reset (ESC[0m) so it is safe to print anywhere.
string ansi
