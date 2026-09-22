package imageembedding

import (
	"math"
	"slices"

	"github.com/gomlx/compute"
	"github.com/gomlx/compute/dtypes"
)

// Pointwise convolutions with one NCHW batch are matrix products over channels.
// Lower them through the public backend interface; other convolutions retain
// the backend's implementation and the ONNX importer still applies any bias.
type pointwiseBackend struct{ compute.Backend }
type pointwiseBuilder struct{ compute.Builder }
type pointwiseFunction struct{ compute.Function }

func (b pointwiseBackend) Builder(name string) compute.Builder {
	return pointwiseBuilder{b.Backend.Builder(name)}
}

func (b pointwiseBuilder) Main() compute.Function {
	return pointwiseFunction{b.Builder.Main()}
}

func unitSpatial(values []int) bool {
	if len(values) != 0 && len(values) != 2 {
		return false
	}
	for _, value := range values {
		if value != 1 {
			return false
		}
	}
	return true
}

func (f pointwiseFunction) ConvGeneral(input, kernel compute.Value, axes compute.ConvolveAxesConfig,
	strides []int, paddings [][2]int, inputDilations, kernelDilations []int,
	channelGroupCount, batchGroupCount int) (compute.Value, error) {
	x, err := f.Shape(input)
	if err != nil {
		return nil, err
	}
	w, err := f.Shape(kernel)
	if err != nil {
		return nil, err
	}
	standardAxes := axes.InputBatch == 0 && axes.InputChannels == 1 && slices.Equal(axes.InputSpatial, []int{2, 3}) &&
		axes.KernelOutputChannels == 0 && axes.KernelInputChannels == 1 && slices.Equal(axes.KernelSpatial, []int{2, 3}) &&
		axes.OutputBatch == 0 && axes.OutputChannels == 1 && slices.Equal(axes.OutputSpatial, []int{2, 3})
	zeroPadding := len(paddings) == 0 || len(paddings) == 2 && paddings[0] == [2]int{} && paddings[1] == [2]int{}
	eligible := standardAxes && x.Rank() == 4 && w.Rank() == 4 &&
		x.DType == dtypes.Float32 && w.DType == dtypes.Float32 &&
		x.Dimensions[0] == 1 && x.Dimensions[1] == w.Dimensions[1] &&
		w.Dimensions[2] == 1 && w.Dimensions[3] == 1 &&
		channelGroupCount == 1 && batchGroupCount == 1 &&
		unitSpatial(strides) && unitSpatial(inputDilations) && unitSpatial(kernelDilations) && zeroPadding
	for _, dimensions := range [][]int{x.Dimensions, w.Dimensions} {
		for _, dimension := range dimensions {
			eligible = eligible && dimension > 0
		}
	}
	if !eligible || x.Dimensions[2] > math.MaxInt/x.Dimensions[3] {
		return f.Function.ConvGeneral(input, kernel, axes, strides, paddings, inputDilations, kernelDilations, channelGroupCount, batchGroupCount)
	}
	weights, err := f.Reshape(kernel, w.Dimensions[0], w.Dimensions[1])
	if err != nil {
		return nil, err
	}
	pixels, err := f.Reshape(input, x.Dimensions[1], x.Dimensions[2]*x.Dimensions[3])
	if err != nil {
		return nil, err
	}
	output, err := f.DotGeneral(weights, []int{1}, nil, pixels, []int{0}, nil, compute.DotGeneralConfig{})
	if err != nil {
		return nil, err
	}
	return f.Reshape(output, 1, w.Dimensions[0], x.Dimensions[2], x.Dimensions[3])
}
