// Package imagepreprocess implements the versioned portable image pixel recipe.
package imagepreprocess

import (
	"bytes"
	"encoding/binary"
	"errors"
	"image"
	"image/color"
	_ "image/jpeg"
	_ "image/png"
	"math"
)

const (
	RecipeVersion  = "exif-white-pillow-bilinear-aa-center-f32-v1"
	MaxImageBytes  = 20 * 1024 * 1024
	MaxImagePixels = 40_000_000
	precision      = 1 << 22
)

type Recipe struct {
	Size               int        `json:"size"`
	ResizeShortestEdge int        `json:"resize_shortest_edge"`
	Mean               [3]float64 `json:"mean"`
	Std                [3]float64 `json:"std"`
	Version            string     `json:"version"`
}

func DefaultRecipe() Recipe {
	return Recipe{Size: 256, ResizeShortestEdge: 256, Std: [3]float64{1, 1, 1}, Version: RecipeVersion}
}

func (r Recipe) Validate() error {
	if r.Version != RecipeVersion || r.Size < 1 || r.Size > r.ResizeShortestEdge || r.ResizeShortestEdge > 2048 {
		return errors.New("unsupported image preprocessing recipe")
	}
	for c := range 3 {
		if math.IsNaN(r.Mean[c]) || math.IsInf(r.Mean[c], 0) || math.Abs(r.Mean[c]) > 1e6 || math.IsNaN(r.Std[c]) || r.Std[c] < 1e-6 || r.Std[c] > 1e6 {
			return errors.New("unsupported image preprocessing recipe")
		}
	}
	return nil
}

func tiffOrientation(body []byte) int {
	if len(body) < 8 {
		return 1
	}
	var order binary.ByteOrder
	switch string(body[:2]) {
	case "II":
		order = binary.LittleEndian
	case "MM":
		order = binary.BigEndian
	default:
		return 1
	}
	if order.Uint16(body[2:4]) != 42 {
		return 1
	}
	offset := uint64(order.Uint32(body[4:8]))
	if offset+2 > uint64(len(body)) {
		return 1
	}
	count := uint64(order.Uint16(body[offset : offset+2]))
	for i := uint64(0); i < count; i++ {
		position := offset + 2 + i*12
		if position+12 > uint64(len(body)) {
			return 1
		}
		entry := body[position : position+12]
		if order.Uint16(entry[:2]) == 274 && order.Uint16(entry[2:4]) == 3 && order.Uint32(entry[4:8]) == 1 {
			value := int(order.Uint16(entry[8:10]))
			if value >= 1 && value <= 8 {
				return value
			}
			return 1
		}
	}
	return 1
}

func orientation(body []byte) (int, error) {
	value := 1
	if bytes.HasPrefix(body, []byte("\x89PNG\r\n\x1a\n")) {
		for offset := uint64(8); offset+12 <= uint64(len(body)); {
			size := uint64(binary.BigEndian.Uint32(body[offset : offset+4]))
			kind := string(body[offset+4 : offset+8])
			end := offset + 12 + size
			if end > uint64(len(body)) {
				return 0, errors.New("invalid PNG image")
			}
			data := body[offset+8 : end-4]
			if kind == "IHDR" && (len(data) != 13 || data[8] > 8) {
				return 0, errors.New("only PNG images up to 8 bits per channel are supported")
			}
			if kind == "acTL" {
				return 0, errors.New("animated PNG images are not supported")
			}
			if kind == "eXIf" {
				value = tiffOrientation(data)
			}
			offset = end
			if kind == "IEND" {
				break
			}
		}
		return value, nil
	}
	if !bytes.HasPrefix(body, []byte{255, 216}) {
		return 0, errors.New("expected a JPEG or PNG image")
	}
	for offset := 2; offset < len(body); {
		if body[offset] != 255 {
			return 0, errors.New("invalid JPEG image")
		}
		for offset < len(body) && body[offset] == 255 {
			offset++
		}
		if offset >= len(body) {
			break
		}
		marker := body[offset]
		offset++
		if marker == 0xda || marker == 0xd9 {
			break
		}
		if marker == 1 || marker >= 0xd0 && marker <= 0xd7 {
			continue
		}
		if offset+2 > len(body) {
			return 0, errors.New("invalid JPEG image")
		}
		size := int(binary.BigEndian.Uint16(body[offset : offset+2]))
		if size < 2 || offset+size > len(body) {
			return 0, errors.New("invalid JPEG image")
		}
		data := body[offset+2 : offset+size]
		if marker == 0xe1 && bytes.HasPrefix(data, []byte("Exif\x00\x00")) {
			value = tiffOrientation(data[6:])
		}
		if marker == 0xe2 && bytes.HasPrefix(data, []byte("MPF\x00")) {
			return 0, errors.New("multi-picture JPEG images are not supported")
		}
		offset += size
	}
	return value, nil
}

type rgbImage struct {
	data          []byte
	width, height int
}

func decode(body []byte) (rgbImage, error) {
	if len(body) == 0 || len(body) > MaxImageBytes {
		return rgbImage{}, errors.New("image exceeds the encoded byte limit or is empty")
	}
	orient, err := orientation(body)
	if err != nil {
		return rgbImage{}, err
	}
	config, format, err := image.DecodeConfig(bytes.NewReader(body))
	if err != nil {
		return rgbImage{}, errors.New("invalid or truncated image")
	}
	if format != "jpeg" && format != "png" {
		return rgbImage{}, errors.New("expected a JPEG or PNG image")
	}
	if config.Width <= 0 || config.Height <= 0 || int64(config.Width)*int64(config.Height) > MaxImagePixels {
		return rgbImage{}, errors.New("image exceeds the decoded pixel limit")
	}
	decoded, _, err := image.Decode(bytes.NewReader(body))
	if err != nil {
		return rgbImage{}, errors.New("invalid or truncated image")
	}
	w, h := config.Width, config.Height
	width, height := w, h
	if orient >= 5 {
		width, height = h, w
	}
	result := rgbImage{make([]byte, width*height*3), width, height}
	for y := range height {
		for x := range width {
			sx, sy := x, y
			switch orient {
			case 2:
				sx = w - 1 - x
			case 3:
				sx, sy = w-1-x, h-1-y
			case 4:
				sy = h - 1 - y
			case 5:
				sx, sy = y, x
			case 6:
				sx, sy = y, h-1-x
			case 7:
				sx, sy = w-1-y, h-1-x
			case 8:
				sx, sy = w-1-y, x
			}
			pixel := color.NRGBAModel.Convert(decoded.At(sx, sy)).(color.NRGBA)
			a := int(pixel.A)
			index := (y*width + x) * 3
			for c, component := range [3]byte{pixel.R, pixel.G, pixel.B} {
				result.data[index+c] = byte((int(component)*a + 255*(255-a) + 127) / 255)
			}
		}
	}
	return result, nil
}

type coefficient struct {
	start   int
	weights []int64
}

func coefficients(source, target, start, count int) []coefficient {
	scale := float64(source) / float64(target)
	support := math.Max(1, scale)
	inverse := 1 / support
	result := make([]coefficient, count)
	for output := range count {
		center := (float64(output+start) + 0.5) * scale
		first := max(0, int(center-support+0.5))
		end := min(source, int(center+support+0.5))
		weights := make([]float64, end-first)
		total := 0.0
		for index := range weights {
			weights[index] = math.Max(0, 1-math.Abs((float64(first+index)-center+0.5)*inverse))
			total += weights[index]
		}
		fixed := make([]int64, len(weights))
		for index, weight := range weights {
			fixed[index] = int64(0.5 + weight/total*precision)
		}
		result[output] = coefficient{first, fixed}
	}
	return result
}

func roundedByte(value int64) byte {
	return byte(min(int64(255), max(int64(0), (value+(precision>>1))>>22)))
}

func resizeCrop(decoded rgbImage, recipe Recipe) []byte {
	shortest := min(decoded.width, decoded.height)
	width := decoded.width * recipe.ResizeShortestEdge / shortest
	height := decoded.height * recipe.ResizeShortestEdge / shortest
	left := int(math.RoundToEven(float64(width-recipe.Size) / 2))
	top := int(math.RoundToEven(float64(height-recipe.Size) / 2))
	xs := coefficients(decoded.width, width, left, recipe.Size)
	ys := coefficients(decoded.height, height, top, recipe.Size)
	first, last := decoded.height, 0
	for _, row := range ys {
		first = min(first, row.start)
		last = max(last, row.start+len(row.weights))
	}
	intermediate := make([]byte, (last-first)*recipe.Size*3)
	for y := first; y < last; y++ {
		for x, col := range xs {
			var sums [3]int64
			for offset, weight := range col.weights {
				index := (y*decoded.width + col.start + offset) * 3
				for c := range 3 {
					sums[c] += int64(decoded.data[index+c]) * weight
				}
			}
			index := ((y-first)*recipe.Size + x) * 3
			for c := range 3 {
				intermediate[index+c] = roundedByte(sums[c])
			}
		}
	}
	result := make([]byte, recipe.Size*recipe.Size*3)
	for y, row := range ys {
		for x := range recipe.Size {
			var sums [3]int64
			for offset, weight := range row.weights {
				index := ((row.start+offset-first)*recipe.Size + x) * 3
				for c := range 3 {
					sums[c] += int64(intermediate[index+c]) * weight
				}
			}
			index := (y*recipe.Size + x) * 3
			for c := range 3 {
				result[index+c] = roundedByte(sums[c])
			}
		}
	}
	return result
}

// Preprocess returns contiguous RGB planes in CHW order, with no batch axis.
// Each pass rounds to uint8; normalization uses separate float32 operations.
func Preprocess(body []byte, recipe Recipe) ([]float32, error) {
	if err := recipe.Validate(); err != nil {
		return nil, err
	}
	decoded, err := decode(body)
	if err != nil {
		return nil, err
	}
	pixels := resizeCrop(decoded, recipe)
	plane := recipe.Size * recipe.Size
	result := make([]float32, plane*3)
	for c := range 3 {
		for p := range plane {
			value := float32(pixels[p*3+c]) / float32(255)
			value = value - float32(recipe.Mean[c])
			result[c*plane+p] = value / float32(recipe.Std[c])
		}
	}
	return result, nil
}
