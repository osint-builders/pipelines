package imageembedding_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gomlx/compute-onnx/support/protos"
	"github.com/osint-builders/pipelines/cli/internal/imageembedding"
	"google.golang.org/protobuf/proto"
)

func TestEncodeAndClose(t *testing.T) {
	filename, spec := writeModel(t, averageModel())
	encoder, err := imageembedding.New(context.Background(), filename, spec)
	if err != nil {
		t.Fatal(err)
	}
	defer encoder.Close()
	input := []float32{3, 3, 3, 3, 4, 4, 4, 4, 0, 0, 0, 0}
	for range 2 {
		result, err := encoder.Encode(context.Background(), input)
		if err != nil {
			t.Fatal(err)
		}
		for i, expected := range []float32{3, 4, 0} {
			if result.Raw[i] != expected || math.Abs(float64(result.Normalized[i]-expected/5)) > 1e-7 {
				t.Fatalf("unexpected embedding: %+v", result)
			}
		}
		result.Raw[0], result.Normalized[0] = 99, 99
	}
	if input[0] != 3 {
		t.Fatal("input tensor was mutated")
	}
	if err := encoder.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := encoder.Encode(context.Background(), input); err == nil || !strings.Contains(err.Error(), "closed") {
		t.Fatalf("encode after close: %v", err)
	}
	if err := encoder.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestRejectsMismatchedContract(t *testing.T) {
	filename, spec := writeModel(t, averageModel())
	for _, test := range []struct {
		name string
		edit func(*imageembedding.Spec)
	}{
		{"missing hash", func(s *imageembedding.Spec) { s.ModelSHA256 = "" }},
		{"wrong hash", func(s *imageembedding.Spec) { s.ModelSHA256 = strings.Repeat("0", 64) }},
		{"uppercase hash", func(s *imageembedding.Spec) { s.ModelSHA256 = strings.ToUpper(s.ModelSHA256) }},
		{"wrong input", func(s *imageembedding.Spec) { s.InputName = "pixels" }},
		{"wrong output", func(s *imageembedding.Spec) { s.OutputName = "logits" }},
		{"wrong input shape", func(s *imageembedding.Spec) { s.Height = 3 }},
		{"wrong dimensions", func(s *imageembedding.Spec) { s.Dimensions = 4 }},
		{"excessive dimensions", func(s *imageembedding.Spec) { s.Dimensions = 4097 }},
		{"invalid size", func(s *imageembedding.Spec) { s.Width = math.MaxInt }},
	} {
		t.Run(test.name, func(t *testing.T) {
			modified := spec
			test.edit(&modified)
			encoder, err := imageembedding.New(context.Background(), filename, modified)
			if err == nil || encoder != nil {
				t.Fatalf("bad contract accepted: %v", err)
			}
		})
	}
}

func TestRejectsInvalidTensorAndEmbedding(t *testing.T) {
	filename, spec := writeModel(t, averageModel())
	encoder, err := imageembedding.New(context.Background(), filename, spec)
	if err != nil {
		t.Fatal(err)
	}
	defer encoder.Close()
	for _, input := range [][]float32{
		nil,
		make([]float32, 11),
		make([]float32, 12),
		{float32(math.NaN()), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
		{float32(math.Inf(1)), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
		{1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13, 1e-13},
	} {
		if _, err := encoder.Encode(context.Background(), input); err == nil {
			t.Fatal("invalid tensor or zero embedding accepted")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := encoder.Encode(ctx, make([]float32, 12)); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled encode: %v", err)
	}
	if _, err := imageembedding.New(ctx, filename, spec); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled load: %v", err)
	}
}

func TestRejectsDynamicShapesAndAdditionalOutputs(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(*protos.ModelProto)
	}{
		{"dynamic input", func(m *protos.ModelProto) {
			m.Graph.Input[0].Type.GetTensorType().Shape.Dim[0].Value = &protos.TensorShapeProto_Dimension_DimParam{DimParam: "batch"}
		}},
		{"dynamic output", func(m *protos.ModelProto) {
			m.Graph.Output[0].Type.GetTensorType().Shape.Dim[1].Value = &protos.TensorShapeProto_Dimension_DimParam{DimParam: "features"}
		}},
		{"additional output", func(m *protos.ModelProto) {
			m.Graph.Output = append(m.Graph.Output, valueInfo("pooled", 1, 3, 1, 1))
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			fixture := averageModel()
			test.edit(fixture)
			filename, spec := writeModel(t, fixture)
			if encoder, err := imageembedding.New(context.Background(), filename, spec); err == nil || encoder != nil {
				t.Fatalf("nonfixed image interface accepted: %v", err)
			}
		})
	}
}

func TestFloat16WeightStorageWithFloat32Execution(t *testing.T) {
	fixture := averageModel()
	fixture.Graph.Initializer = []*protos.TensorProto{{
		Name: "half_bias", Dims: []int64{3}, DataType: int32(protos.TensorProto_FLOAT16),
		RawData: []byte{0, 0x3c, 0, 0x40, 0, 0x42}, // 1, 2, 3 in binary16.
	}}
	fixture.Graph.Node[1].Output[0] = "average"
	fixture.Graph.Node = append(fixture.Graph.Node,
		&protos.NodeProto{OpType: "Cast", Input: []string{"half_bias"}, Output: []string{"bias"},
			Attribute: []*protos.AttributeProto{{Name: "to", Type: protos.AttributeProto_INT, I: int64(protos.TensorProto_FLOAT)}}},
		&protos.NodeProto{OpType: "Add", Input: []string{"average", "bias"}, Output: []string{"embedding"}},
	)
	filename, spec := writeModel(t, fixture)
	encoder, err := imageembedding.New(context.Background(), filename, spec)
	if err != nil {
		t.Fatal(err)
	}
	defer encoder.Close()
	result, err := encoder.Encode(context.Background(), make([]float32, 12))
	if err != nil {
		t.Fatal(err)
	}
	for i, expected := range []float32{1, 2, 3} {
		if result.Raw[i] != expected {
			t.Fatalf("wrong FP16 cast result: %v", result.Raw)
		}
	}
}

func TestRejectsExternalWeights(t *testing.T) {
	fixture := averageModel()
	fixture.Graph.Initializer = []*protos.TensorProto{{
		Name: "external", Dims: []int64{3}, DataType: int32(protos.TensorProto_FLOAT),
		DataLocation: protos.TensorProto_EXTERNAL,
		ExternalData: []*protos.StringStringEntryProto{{Key: "location", Value: "../outside.weights"}},
	}}
	filename, spec := writeModel(t, fixture)
	if _, err := imageembedding.New(context.Background(), filename, spec); err == nil || !strings.Contains(err.Error(), "external data") {
		t.Fatalf("unverified external weights: %v", err)
	}
}

func TestInvalidGraphsReturnErrors(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(*protos.ModelProto)
	}{
		{"unsupported operator", func(m *protos.ModelProto) { m.Graph.Node[0].OpType = "UnknownImageOp" }},
		{"wrong runtime output", func(m *protos.ModelProto) {
			m.Graph.Output[0] = valueInfo("embedding", 1, 4)
		}},
		{"nonfinite output", func(m *protos.ModelProto) {
			m.Graph.Node[1].Output[0] = "average"
			m.Graph.Initializer = []*protos.TensorProto{{Name: "bad", Dims: []int64{3}, DataType: int32(protos.TensorProto_FLOAT), FloatData: []float32{float32(math.Inf(1)), 1, 1}}}
			m.Graph.Node = append(m.Graph.Node, &protos.NodeProto{OpType: "Add", Input: []string{"average", "bad"}, Output: []string{"embedding"}})
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			fixture := averageModel()
			test.edit(fixture)
			filename, spec := writeModel(t, fixture)
			if test.name == "wrong runtime output" {
				spec.Dimensions = 4
			}
			encoder, err := imageembedding.New(context.Background(), filename, spec)
			if err != nil {
				t.Fatal(err)
			}
			defer encoder.Close()
			if _, err := encoder.Encode(context.Background(), make([]float32, 12)); err == nil {
				t.Fatal("invalid graph accepted")
			}
		})
	}
}

func TestRejectsMalformedModel(t *testing.T) {
	filename, spec := writeModel(t, averageModel())
	contents := []byte("not an ONNX model")
	if err := os.WriteFile(filename, contents, 0600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(contents)
	spec.ModelSHA256 = hex.EncodeToString(digest[:])
	if encoder, err := imageembedding.New(context.Background(), filename, spec); err == nil || encoder != nil {
		t.Fatalf("invalid ONNX accepted: %v", err)
	}
}

func averageModel() *protos.ModelProto {
	return &protos.ModelProto{
		IrVersion: 8, OpsetImport: []*protos.OperatorSetIdProto{{Version: 17}},
		Graph: &protos.GraphProto{
			Name:   "tiny_image_encoder",
			Input:  []*protos.ValueInfoProto{valueInfo("image", 1, 3, 2, 2)},
			Output: []*protos.ValueInfoProto{valueInfo("embedding", 1, 3)},
			Node: []*protos.NodeProto{
				{OpType: "GlobalAveragePool", Input: []string{"image"}, Output: []string{"pooled"}},
				{OpType: "Flatten", Input: []string{"pooled"}, Output: []string{"embedding"}},
			},
		},
	}
}

func valueInfo(name string, dimensions ...int64) *protos.ValueInfoProto {
	dims := make([]*protos.TensorShapeProto_Dimension, len(dimensions))
	for i, dimension := range dimensions {
		dims[i] = &protos.TensorShapeProto_Dimension{Value: &protos.TensorShapeProto_Dimension_DimValue{DimValue: dimension}}
	}
	return &protos.ValueInfoProto{Name: name, Type: &protos.TypeProto{Value: &protos.TypeProto_TensorType{
		TensorType: &protos.TypeProto_Tensor{ElemType: int32(protos.TensorProto_FLOAT), Shape: &protos.TensorShapeProto{Dim: dims}},
	}}}
}

func writeModel(t *testing.T, fixture *protos.ModelProto) (string, imageembedding.Spec) {
	t.Helper()
	contents, err := proto.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	filename := filepath.Join(t.TempDir(), "image.onnx")
	if err := os.WriteFile(filename, contents, 0600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(contents)
	return filename, imageembedding.Spec{ModelSHA256: hex.EncodeToString(digest[:]), InputName: "image", OutputName: "embedding", Height: 2, Width: 2, Dimensions: 3}
}
