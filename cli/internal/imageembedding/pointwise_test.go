package imageembedding

import (
	"math"
	"math/rand/v2"
	"slices"
	"testing"

	"github.com/gomlx/compute"
	"github.com/gomlx/compute/dtypes"
	"github.com/gomlx/compute/gobackend"
	"github.com/gomlx/gomlx/core/graph"
	"github.com/gomlx/gomlx/core/tensors"
)

type convCalls struct{ convolutions, products int }
type recordingBackend struct {
	compute.Backend
	calls *convCalls
}
type recordingBuilder struct {
	compute.Builder
	calls *convCalls
}
type recordingFunction struct {
	compute.Function
	calls *convCalls
}

func (b recordingBackend) Builder(name string) compute.Builder {
	return recordingBuilder{b.Backend.Builder(name), b.calls}
}
func (b recordingBuilder) Main() compute.Function {
	return recordingFunction{b.Builder.Main(), b.calls}
}
func (f recordingFunction) ConvGeneral(input, kernel compute.Value, axes compute.ConvolveAxesConfig,
	strides []int, paddings [][2]int, inputDilations, kernelDilations []int,
	channelGroupCount, batchGroupCount int) (compute.Value, error) {
	f.calls.convolutions++
	return f.Function.ConvGeneral(input, kernel, axes, strides, paddings, inputDilations, kernelDilations, channelGroupCount, batchGroupCount)
}
func (f recordingFunction) DotGeneral(lhs compute.Value, lhsContractingAxes, lhsBatchAxes []int,
	rhs compute.Value, rhsContractingAxes, rhsBatchAxes []int, config compute.DotGeneralConfig) (compute.Value, error) {
	f.calls.products++
	return f.Function.DotGeneral(lhs, lhsContractingAxes, lhsBatchAxes, rhs, rhsContractingAxes, rhsBatchAxes, config)
}

func TestPointwiseConvolutionMatchesBackend(t *testing.T) {
	nchw := compute.ConvolveAxesConfig{InputBatch: 0, InputChannels: 1, InputSpatial: []int{2, 3},
		KernelOutputChannels: 0, KernelInputChannels: 1, KernelSpatial: []int{2, 3},
		OutputBatch: 0, OutputChannels: 1, OutputSpatial: []int{2, 3}}
	nhwc := compute.ConvolveAxesConfig{InputBatch: 0, InputChannels: 3, InputSpatial: []int{1, 2},
		KernelOutputChannels: 3, KernelInputChannels: 2, KernelSpatial: []int{0, 1},
		OutputBatch: 0, OutputChannels: 3, OutputSpatial: []int{1, 2}}
	rank3 := compute.ConvolveAxesConfig{InputBatch: 0, InputChannels: 1, InputSpatial: []int{2},
		KernelOutputChannels: 0, KernelInputChannels: 1, KernelSpatial: []int{2},
		OutputBatch: 0, OutputChannels: 1, OutputSpatial: []int{2}}
	for _, test := range []struct {
		name                                     string
		input, kernel                            []int
		axes                                     compute.ConvolveAxesConfig
		strides, inputDilations, kernelDilations []int
		paddings                                 [][2]int
		groups                                   int
		double, lower                            bool
	}{
		{name: "pointwise", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, lower: true},
		{name: "tiled matrix", input: []int{1, 64, 24, 24}, kernel: []int{128, 64, 1, 1}, axes: nchw, lower: true},
		{name: "explicit identity settings", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, strides: []int{1, 1}, inputDilations: []int{1, 1}, kernelDilations: []int{1, 1}, paddings: [][2]int{{0, 0}, {0, 0}}, lower: true},
		{name: "batch fallback", input: []int{2, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw},
		{name: "stride fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, strides: []int{2, 2}},
		{name: "padding fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, paddings: [][2]int{{1, 1}, {2, 0}}},
		{name: "depthwise fallback", input: []int{1, 3, 5, 7}, kernel: []int{3, 1, 1, 1}, axes: nchw, groups: 3},
		{name: "kernel fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 3, 3}, axes: nchw},
		{name: "input dilation fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, inputDilations: []int{2, 2}},
		{name: "kernel dilation fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, kernelDilations: []int{2, 2}},
		{name: "layout fallback", input: []int{1, 5, 7, 3}, kernel: []int{1, 1, 3, 4}, axes: nhwc},
		{name: "rank fallback", input: []int{1, 3, 7}, kernel: []int{4, 3, 1}, axes: rank3},
		{name: "dtype fallback", input: []int{1, 3, 5, 7}, kernel: []int{4, 3, 1, 1}, axes: nchw, double: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			rng := rand.New(rand.NewPCG(41, 73))
			data := func(dimensions []int) []float32 {
				size := 1
				for _, dimension := range dimensions {
					size *= dimension
				}
				values := make([]float32, size)
				for i := range values {
					values[i] = rng.Float32()*4 - 2
				}
				return values
			}
			xValues, wValues := data(test.input), data(test.kernel)
			run := func(lower bool) ([]int, []float32, convCalls) {
				base, err := gobackend.New("")
				if err != nil {
					t.Fatal(err)
				}
				defer base.Finalize()
				x := tensors.FromFlatDataAndDimensions(slices.Clone(xValues), test.input...)
				w := tensors.FromFlatDataAndDimensions(slices.Clone(wValues), test.kernel...)
				defer x.FinalizeAll()
				defer w.FinalizeAll()
				calls := convCalls{}
				var backend compute.Backend = recordingBackend{base, &calls}
				if lower {
					backend = pointwiseBackend{backend}
				}
				exec, err := graph.NewExec(backend, func(input, kernel *graph.Node) *graph.Node {
					if test.double {
						input, kernel = graph.ConvertDType(input, dtypes.Float64), graph.ConvertDType(kernel, dtypes.Float64)
					}
					groups := max(1, test.groups)
					out := graph.ConvGeneral(input, kernel, test.axes, test.strides, test.paddings, test.inputDilations, test.kernelDilations, groups, 1)
					return graph.ConvertDType(out, dtypes.Float32)
				})
				if err != nil {
					t.Fatal(err)
				}
				defer exec.Finalize()
				result, err := exec.Call1(x, w)
				if err != nil {
					t.Fatal(err)
				}
				defer result.FinalizeAll()
				var values []float32
				if err := tensors.ConstFlatData(result, func(flat []float32) { values = slices.Clone(flat) }); err != nil {
					t.Fatal(err)
				}
				return slices.Clone(result.Shape().Dimensions), values, calls
			}
			shape, expected, _ := run(false)
			actualShape, actual, calls := run(true)
			if !slices.Equal(actualShape, shape) || len(actual) != len(expected) {
				t.Fatalf("shape changed: %v != %v", actualShape, shape)
			}
			for i, want := range expected {
				if math.Abs(float64(actual[i]-want)) > 2e-5*(1+math.Abs(float64(want))) {
					t.Fatalf("element %d: %g != %g", i, actual[i], want)
				}
			}
			wantCalls := convCalls{convolutions: 1}
			if test.lower {
				wantCalls = convCalls{products: 1}
			}
			if calls != wantCalls {
				t.Fatalf("unexpected backend path: got %+v want %+v", calls, wantCalls)
			}
		})
	}
}
