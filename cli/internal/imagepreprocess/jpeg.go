package imagepreprocess

import "image"

// JPEG v2 uses Pillow/libjpeg's centered triangle chroma interpolation. The
// standard decoder exposes the original planes; its At method uses replication.
// Edge samples extend to the boundary, with alternating integer rounding.
func jpegChroma(im *image.YCbCr, x, y int) (int, int) {
	offset := im.COffset(x, y)
	cb, cr := int(im.Cb[offset]), int(im.Cr[offset])
	x -= im.Rect.Min.X
	y -= im.Rect.Min.Y
	width, height := im.Rect.Dx(), im.Rect.Dy()
	switch im.SubsampleRatio {
	case image.YCbCrSubsampleRatio422, image.YCbCrSubsampleRatio420:
		columns := (width + 1) / 2
		if columns <= 2 {
			return cb, cr
		}
		nx := max(0, min(columns-1, x/2+2*(x&1)-1)) - x/2
		if im.SubsampleRatio == image.YCbCrSubsampleRatio422 {
			bias := 1 + (x & 1)
			return (3*cb + int(im.Cb[offset+nx]) + bias) >> 2,
				(3*cr + int(im.Cr[offset+nx]) + bias) >> 2
		}
		rows := (height + 1) / 2
		ny := (max(0, min(rows-1, y/2+2*(y&1)-1)) - y/2) * im.CStride
		bias := 8 - (x & 1)
		return (9*cb + 3*int(im.Cb[offset+nx]) + 3*int(im.Cb[offset+ny]) + int(im.Cb[offset+ny+nx]) + bias) >> 4,
			(9*cr + 3*int(im.Cr[offset+nx]) + 3*int(im.Cr[offset+ny]) + int(im.Cr[offset+ny+nx]) + bias) >> 4
	case image.YCbCrSubsampleRatio440:
		rows := (height + 1) / 2
		ny := (max(0, min(rows-1, y/2+2*(y&1)-1)) - y/2) * im.CStride
		bias := 1 + (y & 1)
		return (3*cb + int(im.Cb[offset+ny]) + bias) >> 2,
			(3*cr + int(im.Cr[offset+ny]) + bias) >> 2
	default:
		return cb, cr
	}
}

func jpegRGB(im *image.YCbCr, x, y int) (byte, byte, byte) {
	cb, cr := jpegChroma(im, x, y)
	cb, cr = cb-128, cr-128
	yy := int(im.Y[im.YOffset(x, y)])
	// BT.601 full-range conversion, rounded at 16-bit precision before clamping.
	r := yy + ((91881*cr + 32768) >> 16)
	g := yy + ((-22554*cb - 46802*cr + 32768) >> 16)
	b := yy + ((116130*cb + 32768) >> 16)
	return byte(max(0, min(255, r))), byte(max(0, min(255, g))), byte(max(0, min(255, b)))
}
