package imagepreprocess

import (
	"image"
	"testing"
)

func TestJPEGChromaEdges(t *testing.T) {
	for _, test := range []struct {
		name          string
		width, height int
		ratio         image.YCbCrSubsampleRatio
		cb, expected  []byte
	}{
		{"horizontal", 6, 1, image.YCbCrSubsampleRatio422, []byte{10, 30, 60}, []byte{10, 15, 25, 38, 52, 60}},
		{"vertical", 3, 4, image.YCbCrSubsampleRatio440, []byte{10, 30, 60, 80, 100, 130}, []byte{10, 30, 60, 28, 48, 78, 62, 82, 112, 80, 100, 130}},
		{"both single row", 6, 1, image.YCbCrSubsampleRatio420, []byte{10, 30, 60}, []byte{10, 15, 25, 37, 53, 60}},
		{"quarter horizontal", 9, 1, image.YCbCrSubsampleRatio411, []byte{10, 30, 60}, []byte{10, 10, 10, 10, 30, 30, 30, 30, 60}},
		{"quarter both", 5, 3, image.YCbCrSubsampleRatio410, []byte{10, 30, 80, 100}, []byte{10, 10, 10, 10, 30, 10, 10, 10, 10, 30, 80, 80, 80, 80, 100}},
	} {
		t.Run(test.name, func(t *testing.T) {
			im := image.NewYCbCr(image.Rect(0, 0, test.width, test.height), test.ratio)
			copy(im.Cb, test.cb)
			for i := range im.Cr {
				im.Cr[i] = 128
			}
			for y := range test.height {
				for x := range test.width {
					cb, cr := jpegChroma(im, x, y)
					if cb != int(test.expected[y*test.width+x]) || cr != 128 {
						t.Fatalf("%d,%d: chroma %d,%d", x, y, cb, cr)
					}
				}
			}
		})
	}
}
