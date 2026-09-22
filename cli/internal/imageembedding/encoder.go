// Package imageembedding executes a pinned image model entirely in Go.
package imageembedding

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"slices"
	"strings"
	"sync"

	"github.com/gomlx/compute"
	"github.com/gomlx/compute/dtypes"
	"github.com/gomlx/compute/gobackend"
	"github.com/gomlx/compute/shapes"
	"github.com/gomlx/gomlx/core/graph"
	"github.com/gomlx/gomlx/core/tensors"
	"github.com/gomlx/gomlx/ml/model"
	"github.com/gomlx/onnx-gomlx/onnx"
	"github.com/gomlx/onnx-gomlx/onnx/parser"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

// Spec fixes the graph contract. Inputs are preprocessed RGB float32 NCHW,
// with batch size one; preprocessing belongs to the model's pinned recipe.
type Spec struct {
	ModelSHA256 string `json:"model_sha256"`
	InputName   string `json:"input_name"`
	OutputName  string `json:"output_name"`
	Height      int    `json:"height"`
	Width       int    `json:"width"`
	Dimensions  int    `json:"dimensions"`
}

// Result retains the graph output for parity measurements and its unit vector
// for retrieval. These slices are owned by the caller.
type Result struct {
	Raw        []float32 `json:"raw"`
	Normalized []float32 `json:"normalized"`
}

type Encoder struct {
	mu      sync.Mutex
	spec    Spec
	recipe  imagepreprocess.Recipe
	parsed  onnx.Model
	store   *model.Store
	backend compute.Backend
	exec    *model.Exec
	closed  bool
}

// New loads one self-contained ONNX file, verifies its bytes before parsing,
// and validates its declared inputs and output. Compilation occurs on Encode.
func New(ctx context.Context, filename string, spec Spec) (encoder *Encoder, err error) {
	if err := validateSpec(spec); err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	file, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	contents, err := readModel(file)
	if err != nil {
		return nil, err
	}
	return newModel(ctx, contents, spec)
}

const maxModelBytes = 512 << 20

func readModel(reader io.Reader) ([]byte, error) {
	contents, err := io.ReadAll(io.LimitReader(reader, maxModelBytes+1))
	if err != nil {
		return nil, err
	}
	if len(contents) > maxModelBytes {
		return nil, errors.New("image model exceeds 512 MiB")
	}
	return contents, nil
}

func newModel(ctx context.Context, contents []byte, spec Spec) (encoder *Encoder, err error) {
	if err = validateSpec(spec); err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	e := &Encoder{spec: spec}
	defer func() {
		if failure := recover(); failure != nil {
			err = fmt.Errorf("load image model: %v", failure)
		}
		if err != nil {
			_ = e.Close()
			encoder = nil
		}
	}()
	digest := sha256.Sum256(contents)
	if hex.EncodeToString(digest[:]) != strings.ToLower(spec.ModelSHA256) {
		return nil, errors.New("image model SHA-256 mismatch")
	}
	parsed, err := parser.Parse(contents)
	if err != nil {
		return nil, fmt.Errorf("parse image model: %w", err)
	}
	e.parsed = parsed
	// An ONNX hash cannot authenticate a separate weight file.
	e.parsed.WithExternalDataReader(noExternalData{})
	inputNames, inputShapes := e.parsed.Inputs()
	if len(inputNames) != 1 || inputNames[0] != spec.InputName ||
		!compatible(inputShapes[0], []int{1, 3, spec.Height, spec.Width}) {
		return nil, errors.New("image model input does not match float32 NCHW specification")
	}
	outputNames, outputShapes := e.parsed.Outputs()
	if len(outputNames) != 1 || outputNames[0] != spec.OutputName ||
		!compatible(outputShapes[0], []int{1, spec.Dimensions}) {
		return nil, errors.New("image model output does not match float32 embedding specification")
	}
	e.store = model.NewStore()
	if err = e.parsed.VariablesToScope(e.store.RootScope()); err != nil {
		return nil, fmt.Errorf("load image weights: %w", err)
	}
	e.backend, err = gobackend.New("")
	if err != nil {
		return nil, err
	}
	e.backend = pointwiseBackend{e.backend}
	e.exec, err = model.NewExec(e.backend, e.store, func(scope *model.Scope, input *graph.Node) *graph.Node {
		return e.parsed.CallGraph(scope, input.Graph(), map[string]*graph.Node{spec.InputName: input}, spec.OutputName)[0]
	})
	if err != nil {
		return nil, err
	}
	e.exec.SetMaxCache(1)
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return e, nil
}

func (e *Encoder) Recipe() imagepreprocess.Recipe { return e.recipe }

// EncodeImage uses the recipe loaded with NewFromFS and never reads external files.
func (e *Encoder) EncodeImage(ctx context.Context, contents []byte) (Result, error) {
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	tensor, err := imagepreprocess.Preprocess(contents, e.recipe)
	if err != nil {
		return Result{}, err
	}
	return e.Encode(ctx, tensor)
}

// Encode serializes inference with Close. Cancellation is checked before and
// after computation; the Go backend cannot interrupt an executing graph.
func (e *Encoder) Encode(ctx context.Context, chw []float32) (result Result, err error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	defer func() {
		if failure := recover(); failure != nil {
			result = Result{}
			err = fmt.Errorf("execute image model: %v", failure)
		}
	}()
	if e.closed {
		return Result{}, errors.New("image encoder is closed")
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if len(chw) != 3*e.spec.Height*e.spec.Width {
		return Result{}, errors.New("image tensor length does not match specification")
	}
	for _, value := range chw {
		if !finite(value) {
			return Result{}, errors.New("image tensor contains nonfinite values")
		}
	}
	input := tensors.FromFlatDataAndDimensions(chw, 1, 3, e.spec.Height, e.spec.Width)
	defer input.FinalizeAll()
	output, err := e.exec.Call1(input)
	if err != nil {
		return Result{}, fmt.Errorf("execute image model: %w", err)
	}
	defer output.FinalizeAll()
	if !output.Shape().Equal(shapes.Make(dtypes.Float32, 1, e.spec.Dimensions)) {
		return Result{}, errors.New("image model returned an unexpected embedding shape")
	}
	err = tensors.ConstFlatData(output, func(values []float32) {
		result.Raw = append([]float32(nil), values...)
	})
	if err != nil {
		return Result{}, err
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	var squaredNorm float64
	for _, value := range result.Raw {
		if !finite(value) {
			return Result{}, errors.New("image model returned nonfinite values")
		}
		squaredNorm += float64(value) * float64(value)
	}
	norm := math.Sqrt(squaredNorm)
	if norm <= 1e-12 {
		return Result{}, errors.New("image model returned an empty embedding")
	}
	result.Normalized = make([]float32, len(result.Raw))
	for i, value := range result.Raw {
		result.Normalized[i] = float32(float64(value) / norm)
	}
	return result, nil
}

func (e *Encoder) Close() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return nil
	}
	e.closed = true
	if e.exec != nil {
		e.exec.Finalize()
	}
	if e.store != nil {
		e.store.Finalize()
	}
	if e.backend != nil {
		e.backend.Finalize()
	}
	if e.parsed != nil {
		return e.parsed.Close()
	}
	return nil
}

func validateSpec(spec Spec) error {
	digest, err := hex.DecodeString(spec.ModelSHA256)
	if err != nil || len(digest) != sha256.Size || spec.ModelSHA256 != strings.ToLower(spec.ModelSHA256) {
		return errors.New("image model requires a SHA-256 digest")
	}
	if spec.InputName == "" || spec.OutputName == "" || spec.Height < 1 || spec.Height > 4096 ||
		spec.Width < 1 || spec.Width > 4096 || spec.Dimensions < 1 || spec.Dimensions > 4096 {
		return errors.New("invalid image model specification")
	}
	return nil
}

func compatible(shape shapes.Shape, expected []int) bool {
	return shape.DType == dtypes.Float32 && slices.Equal(shape.Dimensions, expected)
}

func finite(value float32) bool { return !math.IsNaN(float64(value)) && !math.IsInf(float64(value), 0) }

type noExternalData struct{}

func (noExternalData) ReadInto(onnx.ExternalDataInfo, []byte) error {
	return errors.New("image model must contain its weights; external data is not hash-pinned")
}
func (noExternalData) Close() error { return nil }
