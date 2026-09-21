package imageembedding_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"math"
	"testing"
	"testing/fstest"

	"github.com/osint-builders/pipelines/cli/internal/imageembedding"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
	"google.golang.org/protobuf/proto"
)

func bundledModel(t *testing.T) (fstest.MapFS, map[string]any) {
	t.Helper()
	graph, err := proto.Marshal(averageModel())
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(graph)
	recipe := imagepreprocess.DefaultRecipe()
	recipe.Size, recipe.ResizeShortestEdge = 2, 2
	manifest := map[string]any{"schema_version": 1, "file": "image.onnx", "bytes": len(graph),
		"sha256": hex.EncodeToString(digest[:]), "input": "image", "output": "embedding",
		"shape": []int{1, 3, 2, 2}, "dimensions": 3, "normalization": "l2", "preprocess": recipe}
	body, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	return fstest.MapFS{"image/model.json": {Data: body}, "image/image.onnx": {Data: graph}}, manifest
}

func TestEncodedImageFromReadOnlyBundle(t *testing.T) {
	files, _ := bundledModel(t)
	encoder, err := imageembedding.NewFromFS(context.Background(), files, "image/model.json")
	if err != nil {
		t.Fatal(err)
	}
	defer encoder.Close()
	pixels := image.NewNRGBA(image.Rect(0, 0, 2, 2))
	for y := range 2 {
		for x := range 2 {
			pixels.SetNRGBA(x, y, color.NRGBA{R: 3, G: 4, A: 255})
		}
	}
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, pixels); err != nil {
		t.Fatal(err)
	}
	result, err := encoder.EncodeImage(context.Background(), encoded.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	for i, expected := range []float32{.6, .8, 0} {
		if math.Abs(float64(result.Normalized[i]-expected)) > 1e-6 {
			t.Fatalf("unexpected encoded image vector: %v", result.Normalized)
		}
	}
	if encoder.Recipe().Size != 2 {
		t.Fatal("manifest recipe was not retained")
	}
	if _, err := encoder.EncodeImage(context.Background(), []byte("invalid")); err == nil {
		t.Fatal("invalid encoded image accepted")
	}
}

func TestBundleRejectsInvalidManifestOrGraph(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(map[string]any, fstest.MapFS)
	}{
		{"future schema", func(m map[string]any, _ fstest.MapFS) { m["schema_version"] = 2 }},
		{"traversal", func(m map[string]any, _ fstest.MapFS) { m["file"] = "../image.onnx" }},
		{"remote graph", func(m map[string]any, _ fstest.MapFS) { m["file"] = "https://example/image.onnx" }},
		{"wrong byte length", func(m map[string]any, _ fstest.MapFS) { m["bytes"] = 1 }},
		{"wrong hash", func(_ map[string]any, f fstest.MapFS) { f["image/image.onnx"].Data[0] ^= 1 }},
		{"recipe shape disagreement", func(m map[string]any, _ fstest.MapFS) { m["shape"] = []int{1, 3, 4, 4} }},
		{"unknown recipe", func(m map[string]any, _ fstest.MapFS) {
			r := m["preprocess"].(imagepreprocess.Recipe)
			r.Version = "future"
			m["preprocess"] = r
		}},
		{"truncated recipe channels", func(m map[string]any, _ fstest.MapFS) {
			m["preprocess"] = map[string]any{"size": 2, "resize_shortest_edge": 2,
				"mean": []float64{0, 0, 0, 1}, "std": []float64{1, 1, 1}, "version": imagepreprocess.RecipeVersion}
		}},
		{"missing recipe channels", func(m map[string]any, _ fstest.MapFS) {
			m["preprocess"] = map[string]any{"size": 2, "resize_shortest_edge": 2,
				"std": []float64{1, 1, 1}, "version": imagepreprocess.RecipeVersion}
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			files, manifest := bundledModel(t)
			test.edit(manifest, files)
			body, err := json.Marshal(manifest)
			if err != nil {
				t.Fatal(err)
			}
			files["image/model.json"].Data = body
			if encoder, err := imageembedding.NewFromFS(context.Background(), files, "image/model.json"); err == nil || encoder != nil {
				t.Fatalf("invalid bundle accepted: %v", err)
			}
		})
	}
}
