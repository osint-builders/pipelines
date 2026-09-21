package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/gomlx/compute-onnx/support/protos"
	"github.com/osint-builders/pipelines/cli/internal/imageembedding"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
	"google.golang.org/protobuf/proto"
)

func TestImageProbeUsesManifestAndPixels(t *testing.T) {
	directory := t.TempDir()
	value := func(name string, dimensions ...int64) *protos.ValueInfoProto {
		dims := make([]*protos.TensorShapeProto_Dimension, len(dimensions))
		for i, dimension := range dimensions {
			dims[i] = &protos.TensorShapeProto_Dimension{Value: &protos.TensorShapeProto_Dimension_DimValue{DimValue: dimension}}
		}
		return &protos.ValueInfoProto{Name: name, Type: &protos.TypeProto{Value: &protos.TypeProto_TensorType{
			TensorType: &protos.TypeProto_Tensor{ElemType: 1, Shape: &protos.TensorShapeProto{Dim: dims}},
		}}}
	}
	model, err := proto.Marshal(&protos.ModelProto{
		IrVersion: 8, OpsetImport: []*protos.OperatorSetIdProto{{Version: 17}},
		Graph: &protos.GraphProto{Name: "probe", Input: []*protos.ValueInfoProto{value("image", 1, 3, 2, 2)},
			Output: []*protos.ValueInfoProto{value("embedding", 1, 3)},
			Node: []*protos.NodeProto{
				{OpType: "GlobalAveragePool", Input: []string{"image"}, Output: []string{"pooled"}},
				{OpType: "Flatten", Input: []string{"pooled"}, Output: []string{"embedding"}},
			}},
	})
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(model)
	writeFile(t, filepath.Join(directory, "model.onnx"), model)
	manifest := manifestFixture()
	manifest["sha256"] = hex.EncodeToString(digest[:])
	manifest["bytes"] = len(model)
	manifestPath := filepath.Join(directory, "model.json")
	writeJSON(t, manifestPath, manifest)
	pixels := image.NewNRGBA(image.Rect(0, 0, 2, 2))
	for y := range 2 {
		for x := range 2 {
			pixels.SetNRGBA(x, y, color.NRGBA{153, 204, 0, 255})
		}
	}
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, pixels); err != nil {
		t.Fatal(err)
	}
	imagePath := filepath.Join(directory, "query.png")
	writeFile(t, imagePath, encoded.Bytes())
	var output bytes.Buffer
	if err := run([]string{"--manifest", manifestPath, "--image", imagePath, "--repeats", "1"}, &output); err != nil {
		t.Fatal(err)
	}
	var result struct {
		imageembedding.Result
		RepeatMS     []float64 `json:"repeat_ms"`
		TensorSHA256 string    `json:"tensor_sha256"`
		TotalFirstMS float64   `json:"total_first_ms"`
	}
	if err := json.Unmarshal(output.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if len(result.RepeatMS) != 1 || len(result.TensorSHA256) != 64 || result.TotalFirstMS <= 0 {
		t.Fatalf("incomplete probe result: %+v", result)
	}
	for i, expected := range []float32{0.6, 0.8, 0} {
		if math.Abs(float64(result.Normalized[i]-expected)) > 1e-6 {
			t.Fatalf("unexpected image embedding: %v", result.Normalized)
		}
	}
}

func TestProbeRejectsInvalidRequests(t *testing.T) {
	manifestPath := filepath.Join(t.TempDir(), "model.json")
	writeFile(t, filepath.Join(filepath.Dir(manifestPath), "model.onnx"), []byte{0})
	for _, test := range []struct {
		name string
		edit func(map[string]any)
	}{
		{"unknown schema", func(m map[string]any) { m["schema_version"] = 2 }},
		{"boolean schema", func(m map[string]any) { m["schema_version"] = true }},
		{"model without extension", func(m map[string]any) { m["file"] = "model" }},
		{"zero bytes", func(m map[string]any) { m["bytes"] = 0 }},
		{"excessive bytes", func(m map[string]any) { m["bytes"] = (512 << 20) + 1 }},
		{"path traversal", func(m map[string]any) { m["file"] = "../model.onnx" }},
		{"windows absolute path", func(m map[string]any) { m["file"] = `C:\model.onnx` }},
		{"wrong layout", func(m map[string]any) { m["shape"] = []int{1, 2, 2, 3} }},
		{"unknown recipe", func(m map[string]any) { m["preprocess"] = imagepreprocess.Recipe{Version: "future"} }},
		{"normalization", func(m map[string]any) { m["normalization"] = "none" }},
	} {
		t.Run(test.name, func(t *testing.T) {
			manifest := manifestFixture()
			test.edit(manifest)
			writeJSON(t, manifestPath, manifest)
			if _, _, _, err := readManifest(manifestPath); err == nil {
				t.Fatal("invalid manifest accepted")
			}
		})
	}
	for _, args := range [][]string{
		{}, {"--image", "image.png"}, {"--model", "model.onnx", "--image", "image.png"},
		{"--manifest", manifestPath, "--image", "image.png", "--tensor", "query.f32"},
		{"--manifest", manifestPath, "--image", "image.png", "--height", "2"},
		{"--manifest", manifestPath, "--image", "image.png", "--repeats", "-1"},
	} {
		var output bytes.Buffer
		if err := run(args, &output); err == nil || output.Len() != 0 {
			t.Fatalf("invalid request accepted: %v", args)
		}
	}
}

func TestManifestChecksBytesAndResolvedPath(t *testing.T) {
	directory := t.TempDir()
	manifestPath := filepath.Join(directory, "model.json")
	writeJSON(t, manifestPath, manifestFixture())
	modelPath := filepath.Join(directory, "model.onnx")
	writeFile(t, modelPath, []byte{0, 1})
	if _, _, _, err := readManifest(manifestPath); err == nil {
		t.Fatal("manifest byte mismatch accepted")
	}
	if err := os.Remove(modelPath); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "outside.onnx")
	writeFile(t, outside, []byte{0})
	if err := os.Symlink(outside, modelPath); err != nil {
		t.Skipf("symlink creation unavailable: %v", err)
	}
	if _, _, _, err := readManifest(manifestPath); err == nil {
		t.Fatal("model symlink outside manifest directory accepted")
	}
}

func manifestFixture() map[string]any {
	return map[string]any{
		"schema_version": 1, "file": "model.onnx", "bytes": 1, "input": "image", "output": "embedding",
		"shape": []int{1, 3, 2, 2}, "dimensions": 3, "normalization": "l2",
		"preprocess": imagepreprocess.Recipe{Size: 2, ResizeShortestEdge: 2, Std: [3]float64{1, 1, 1}, Version: imagepreprocess.RecipeVersion},
	}
}

func writeJSON(t *testing.T, filename string, value any) {
	t.Helper()
	contents, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filename, contents)
}

func writeFile(t *testing.T, filename string, contents []byte) {
	t.Helper()
	if err := os.WriteFile(filename, contents, 0600); err != nil {
		t.Fatal(err)
	}
}
