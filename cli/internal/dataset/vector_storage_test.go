package dataset

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"math"
	"testing"

	"github.com/gomlx/compute/dtypes/float16"
)

func halfVectorFixture(t *testing.T, mutate func(map[string]any, map[string][]byte)) (*Dataset, error) {
	t.Helper()
	base := fixture(t, false)
	members := map[string][]byte{}
	for _, file := range base.Files.File {
		stream, err := file.Open()
		if err != nil {
			t.Fatal(err)
		}
		members[file.Name], err = io.ReadAll(stream)
		stream.Close()
		if err != nil {
			t.Fatal(err)
		}
	}
	var manifest map[string]any
	if err := json.Unmarshal(members["manifest.json"], &manifest); err != nil {
		t.Fatal(err)
	}
	raw := members["vectors.f32"]
	binary.LittleEndian.PutUint32(raw, math.Float32bits(0.6))
	binary.LittleEndian.PutUint32(raw[4:], math.Float32bits(0.8))
	half := make([]byte, len(raw)/2)
	for i := 0; i < len(raw)/4; i++ {
		binary.LittleEndian.PutUint16(half[i*2:], float16.FromFloat32(math.Float32frombits(binary.LittleEndian.Uint32(raw[i*4:]))).Bits())
	}
	delete(members, "vectors.f32")
	members["vectors.f16"] = half
	manifest["text_vectors"] = map[string]any{"member": "vectors.f16", "dtype": "float16-le"}
	if mutate != nil {
		mutate(manifest, members)
	}
	files := map[string]string{}
	for name, body := range members {
		if name != "manifest.json" {
			files[name] = digestString(body)
		}
	}
	manifest["files"] = files
	members["manifest.json"], _ = json.Marshal(manifest)
	var output bytes.Buffer
	w := zip.NewWriter(&output)
	for name, body := range members {
		entry, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = entry.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return Open(output.Bytes())
}

func TestHalfTextVectorsLoadAndNormalize(t *testing.T) {
	d, err := halfVectorFixture(t, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := d.LoadVectors(); err != nil {
		t.Fatal(err)
	}
	if math.Abs(float64(d.vectors[0])-0.6) > 0.0002 || math.Abs(float64(d.vectors[1])-0.8) > 0.0002 {
		t.Fatal(d.vectors[:2])
	}
	norm := float64(d.vectors[0])*float64(d.vectors[0]) + float64(d.vectors[1])*float64(d.vectors[1])
	if math.Abs(norm-1) > 1e-7 {
		t.Fatal("half vectors were not normalized", norm)
	}
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
}

func TestHalfTextVectorsRejectMalformedStorage(t *testing.T) {
	for _, kind := range []string{"null", "unknown", "conflict", "truncated", "nan", "zero"} {
		t.Run(kind, func(t *testing.T) {
			d, err := halfVectorFixture(t, func(manifest map[string]any, members map[string][]byte) {
				switch kind {
				case "null":
					manifest["text_vectors"] = nil
				case "unknown":
					manifest["text_vectors"] = map[string]any{"member": "vectors.f16", "dtype": "int16"}
				case "conflict":
					members["vectors.f32"] = []byte{0}
				case "truncated":
					members["vectors.f16"] = members["vectors.f16"][:2]
				case "nan":
					binary.LittleEndian.PutUint16(members["vectors.f16"], float16.NaN().Bits())
				case "zero":
					clear(members["vectors.f16"])
				}
			})
			if err == nil {
				err = d.LoadVectors()
			}
			if err == nil {
				t.Fatal("invalid text vector storage accepted")
			}
		})
	}
}
