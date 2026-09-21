package imagepreprocess

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"hash/crc32"
	"image"
	"image/color"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"testing"
)

// Optional local archive probes keep downloaded images out of test fixtures.
func TestLocalImageParity(t *testing.T) {
	manifest := os.Getenv("PIPELINES_IMAGE_PARITY_MANIFEST")
	if manifest == "" {
		t.Skip("no local image parity manifest")
	}
	body, err := os.ReadFile(manifest)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Recipe Recipe `json:"recipe"`
		Probes []struct {
			ID            string `json:"id"`
			ImagePath     string `json:"image_path"`
			ReferencePath string `json:"reference_path"`
		} `json:"probes"`
	}
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	var reports []map[string]any
	for _, probe := range fixture.Probes {
		input, err := os.ReadFile(probe.ImagePath)
		if err != nil {
			t.Fatal(err)
		}
		expected, err := os.ReadFile(probe.ReferencePath)
		if err != nil {
			t.Fatal(err)
		}
		actual, err := Preprocess(input, fixture.Recipe)
		if err != nil {
			t.Fatal(err)
		}
		if len(actual)*4 != len(expected) {
			t.Fatal("local reference shape mismatch")
		}
		maxError, absolute, squared := 0.0, 0.0, 0.0
		for i, value := range actual {
			want := math.Float32frombits(binary.LittleEndian.Uint32(expected[i*4 : i*4+4]))
			difference := float64(value - want)
			maxError = math.Max(maxError, math.Abs(difference))
			absolute += math.Abs(difference)
			squared += difference * difference
		}
		reports = append(reports, map[string]any{"id": probe.ID, "max_abs_error": maxError, "mean_abs_error": absolute / float64(len(actual)), "rmse": math.Sqrt(squared / float64(len(actual)))})
	}
	output, err := json.MarshalIndent(reports, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(filepath.Dir(manifest), "go-pixel-parity.json"), append(output, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}

func TestSharedReferenceProbes(t *testing.T) {
	body, err := os.ReadFile("../../../tests/fixtures/image_preprocess.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Probes []struct {
			Name      string  `json:"name"`
			Recipe    Recipe  `json:"recipe"`
			Encoded   string  `json:"encoded_base64"`
			Expected  string  `json:"expected_f32le_base64"`
			Tolerance float64 `json:"max_abs_error"`
		} `json:"probes"`
	}
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, probe := range fixture.Probes {
		t.Run(probe.Name, func(t *testing.T) {
			encoded, err := base64.StdEncoding.DecodeString(probe.Encoded)
			if err != nil {
				t.Fatal(err)
			}
			expected, err := base64.StdEncoding.DecodeString(probe.Expected)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := Preprocess(encoded, probe.Recipe)
			if err != nil {
				t.Fatal(err)
			}
			if len(actual)*4 != len(expected) {
				t.Fatalf("shape mismatch: %d values", len(actual))
			}
			maxError := 0.0
			for i, value := range actual {
				want := math.Float32frombits(binary.LittleEndian.Uint32(expected[i*4 : i*4+4]))
				maxError = math.Max(maxError, math.Abs(float64(value-want)))
			}
			if maxError > probe.Tolerance {
				t.Fatalf("max pixel error %.9g > %.9g", maxError, probe.Tolerance)
			}
			t.Logf("max normalized pixel error %.9g", maxError)
		})
	}
}

func pngBytes(t *testing.T, input image.Image) []byte {
	t.Helper()
	var stream bytes.Buffer
	if err := png.Encode(&stream, input); err != nil {
		t.Fatal(err)
	}
	return stream.Bytes()
}

func pngChunk(kind string, value []byte) []byte {
	body := make([]byte, len(value)+12)
	binary.BigEndian.PutUint32(body[:4], uint32(len(value)))
	copy(body[4:8], kind)
	copy(body[8:], value)
	binary.BigEndian.PutUint32(body[len(body)-4:], crc32.ChecksumIEEE(body[4:len(body)-4]))
	return body
}

func TestCapsAndBadInput(t *testing.T) {
	valid := pngBytes(t, image.NewNRGBA(image.Rect(0, 0, 2, 3)))
	header := append([]byte(nil), valid[16:29]...)
	binary.BigEndian.PutUint32(header[:4], 10_001)
	binary.BigEndian.PutUint32(header[4:8], 4_000)
	oversized := append(append(append([]byte(nil), valid[:8]...), pngChunk("IHDR", header)...), valid[33:]...)
	animated := append(append(append([]byte(nil), valid[:33]...), pngChunk("acTL", make([]byte, 8))...), valid[33:]...)
	for name, body := range map[string][]byte{
		"empty": nil, "encoded cap": make([]byte, MaxImageBytes+1), "decoded cap": oversized,
		"html": []byte("<html>not an image"), "truncated": valid[:40], "animation": animated,
		"16 bit": pngBytes(t, image.NewGray16(image.Rect(0, 0, 2, 3))),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := Preprocess(body, DefaultRecipe()); err == nil {
				t.Fatal("expected rejection")
			}
		})
	}
}

func TestRecipeValidation(t *testing.T) {
	for _, update := range []func(*Recipe){
		func(r *Recipe) { r.Size = 0 }, func(r *Recipe) { r.Size = 257 }, func(r *Recipe) { r.ResizeShortestEdge = 2049 },
		func(r *Recipe) { r.Version = "unknown" }, func(r *Recipe) { r.Std[0] = 0 }, func(r *Recipe) { r.Mean[0] = math.NaN() },
		func(r *Recipe) { r.Std[0] = math.Inf(1) },
	} {
		recipe := DefaultRecipe()
		update(&recipe)
		if _, err := Preprocess(nil, recipe); err == nil {
			t.Fatal("invalid recipe accepted")
		}
	}
}

func TestAlphaBeforeResampling(t *testing.T) {
	input := image.NewNRGBA(image.Rect(0, 0, 2, 1))
	input.SetNRGBA(0, 0, color.NRGBA{13, 255, 0, 0})
	input.SetNRGBA(1, 0, color.NRGBA{12, 40, 200, 128})
	actual, err := decode(pngBytes(t, input))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual.data, []byte{255, 255, 255, 133, 147, 227}) {
		t.Fatal(actual.data)
	}
}

func TestBoundedExif(t *testing.T) {
	for _, order := range []binary.ByteOrder{binary.LittleEndian, binary.BigEndian} {
		body := make([]byte, 26)
		copy(body, "II")
		if order == binary.BigEndian {
			copy(body, "MM")
		}
		order.PutUint16(body[2:4], 42)
		order.PutUint32(body[4:8], 8)
		order.PutUint16(body[8:10], 1)
		order.PutUint16(body[10:12], 274)
		order.PutUint16(body[12:14], 3)
		order.PutUint32(body[14:18], 1)
		order.PutUint16(body[18:20], 7)
		if tiffOrientation(body) != 7 || tiffOrientation(body[:17]) != 1 {
			t.Fatal("EXIF orientation mismatch")
		}
		order.PutUint32(body[4:8], math.MaxUint32)
		if tiffOrientation(body) != 1 {
			t.Fatal("unbounded EXIF offset")
		}
	}
}

func TestExtremeAspectRatioOnlyMaterializesCrop(t *testing.T) {
	input := image.NewNRGBA(image.Rect(0, 0, 1, 100_001))
	for y := range 100_001 {
		input.SetNRGBA(0, y, color.NRGBA{13, 22, 91, 255})
	}
	recipe := DefaultRecipe()
	recipe.Size = 4
	recipe.ResizeShortestEdge = 2048
	actual, err := Preprocess(pngBytes(t, input), recipe)
	if err != nil {
		t.Fatal(err)
	}
	if len(actual) != 48 || actual[0] != float32(13)/255 || actual[16] != float32(22)/255 || actual[32] != float32(91)/255 {
		t.Fatal(actual)
	}
}
